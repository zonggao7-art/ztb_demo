import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { resultFor } from './demo'
import { initialState, isBusy, reducer } from './state'
import { ChatFailure, requestChat } from './api/chat'
import { HISTORY_KEY, openHistory } from './history'
import type { SaveStatus } from './history'
import type { Mode, Scenario, Turn } from './types'

export function useChat() {
  const [history] = useState(() => openHistory(() => window.localStorage, crypto.randomUUID()))
  const [state, dispatch] = useReducer(reducer, history.initial)
  const [mode, setMode] = useState<Mode>(() => history.initial.conversations.find(c => c.id === history.initial.activeId)?.mode ?? 'live')
  const [saveStatus, setSaveStatus] = useState<SaveStatus>(history.status)
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  const clock = useRef<ReturnType<typeof setInterval> | null>(null)
  const active = useRef<{ conversationId: string; runId: string; started: number; controller?: AbortController } | null>(null)
  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout); timers.current = []
    if (clock.current !== null) clearInterval(clock.current)
    clock.current = null
  }, [])
  useEffect(() => () => { active.current?.controller?.abort(); active.current = null; clear() }, [clear])
  useEffect(() => { setSaveStatus(history.save(state)) }, [history, state])
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if ((event.key === HISTORY_KEY || event.key === null) && history.changedElsewhere()) setSaveStatus('conflict')
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [history])

  const start = (question: string, scenario: Scenario, clearDraft = true) => {
    if (!question.trim() || active.current) return
    clear()
    const target = { conversationId: state.activeId, runId: crypto.randomUUID() }
    const started = performance.now(), elapsed = () => (performance.now() - started) / 1000
    const controller = mode === 'live' ? new AbortController() : undefined
    active.current = { ...target, started, controller }
    const turn: Turn = { id: target.runId, question: question.trim(), scenario, mode, status: 'running', stage: 0, stageLabel: mode === 'live' ? '正在连接服务' : undefined, answer: '', citations: [], records: [], elapsed: 0 }
    dispatch({ type: 'start', conversationId: target.conversationId, turn, clearDraft })
    const later = (ms: number, fn: () => void) => timers.current.push(setTimeout(() => {
      if (active.current?.runId === target.runId) fn()
    }, ms))
    if (controller) {
      let receivedText = false
      const stillCurrent = () => active.current?.runId === target.runId
      clock.current = setInterval(() => { if (stillCurrent()) dispatch({ type: 'tick', ...target, elapsed: elapsed() }) }, 1000)
      // 客户端兜底与后端 90 秒预算区分：这个提示只表明客户端停止等待。
      later(105_000, () => {
        dispatch({ type: 'stop', ...target, status: 'failed', notice: '等待已超过 105 秒，客户端已停止本次请求。未收到最终结果，请重试。', elapsed: elapsed() })
        controller.abort(); active.current = null; clear()
      })
      void requestChat(question.trim(), `web-${target.conversationId}`, controller.signal, {
        bind: serverRequestId => { if (stillCurrent()) dispatch({ type: 'bind', ...target, serverRequestId }) },
        stage: label => { if (stillCurrent()) dispatch({ type: 'live-stage', ...target, label, elapsed: elapsed() }) },
        token: delta => {
          receivedText ||= !!delta
          if (stillCurrent()) {
            dispatch({ type: 'live-stage', ...target, label: '正在接收已校验正文', elapsed: elapsed() })
            dispatch({ type: 'token', ...target, delta, elapsed: elapsed() })
          }
        },
      }).then(result => {
        if (stillCurrent()) dispatch({ type: 'finish', ...target, result, elapsed: elapsed() })
      }).catch(error => {
        if (!stillCurrent()) return
        const failure = error instanceof ChatFailure ? error : new ChatFailure('network', '连接未完成，请重试。')
        const status = failure.kind === 'cancelled' ? 'cancelled'
          : receivedText || failure.kind === 'incomplete' ? 'interrupted' : 'failed'
        dispatch({ type: 'stop', ...target, status, notice: failure.message, elapsed: elapsed() })
      }).finally(() => { if (stillCurrent()) { active.current = null; clear() } })
      return
    }

    // A2 演示路径与真实请求分开，演示内容永远不会发送到 API。
    for (let stage = 1; stage <= 5; stage++) later(stage * 650, () => dispatch({ type: 'stage', ...target, stage, elapsed: elapsed() }))
    if (scenario === 'error' || scenario === 'timeout') {
      later(2400, () => {
        dispatch({ type: 'stop', ...target, status: 'failed', notice: scenario === 'timeout' ? '请求超时，尚未收到完整结果。可以重试。' : '查询服务暂时不可用。可以重试。', elapsed: elapsed() })
        active.current = null; clear()
      })
      return
    }
    const result = resultFor(scenario), chunks = result.answer.match(/[\s\S]{1,80}/g) ?? []
    chunks.forEach((delta, i) => later(3600 + i * 160, () => {
      dispatch({ type: 'token', ...target, delta, elapsed: elapsed() })
      if (scenario === 'disconnect' && i === 0) {
        dispatch({ type: 'stop', ...target, status: 'interrupted', notice: '连接中断，结果未完整接收。已收到的正文为不完整内容。', elapsed: elapsed() })
        active.current = null; clear()
      }
    }))
    later(3700 + chunks.length * 160, () => {
      dispatch({ type: 'finish', ...target, result, elapsed: elapsed() })
      active.current = null; clear()
    })
  }
  const cancel = useCallback(() => {
    const run = active.current
    if (!run) return
    clear(); active.current = null; run.controller?.abort()
    dispatch({ type: 'stop', conversationId: run.conversationId, runId: run.runId, status: 'cancelled', notice: '已停止。本轮未接收完成，已有内容仅作保留。', elapsed: (performance.now() - run.started) / 1000 })
  }, [clear])
  const conversation = state.conversations.find(c => c.id === state.activeId)!
  return {
    state, dispatch, conversation, start, cancel, mode, saveStatus,
    clearHistory: () => {
      const fresh = initialState(crypto.randomUUID())
      fresh.conversations[0].mode = mode
      const saved = history.save(fresh, true)
      setSaveStatus(saved)
      if (saved !== 'saved') return false
      cancel()
      dispatch({ type: 'reset', state: fresh })
      return true
    },
    busy: conversation.turns.some(t => isBusy(t.status)),
    newConversation: () => { cancel(); dispatch({ type: 'new', id: crypto.randomUUID(), mode }) },
    selectConversation: (id: string) => {
      const next = state.conversations.find(c => c.id === id)
      if (next && id !== state.activeId) {
        cancel()
        const nextMode = next.mode ?? 'demo'
        if (nextMode !== mode) dispatch({ type: 'draft', value: '' })
        setMode(nextMode); dispatch({ type: 'select', id })
      }
    },
    changeMode: (next: Mode) => {
      if (mode === next) return
      cancel(); setMode(next); dispatch({ type: 'draft', value: '' }); dispatch({ type: 'new', id: crypto.randomUUID(), mode: next })
    },
  }
}
