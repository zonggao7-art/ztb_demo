import { describe, expect, it } from 'vitest'
import { HISTORY_KEY, openHistory } from './history'
import { initialState, reducer } from './state'
import { resultFor } from './demo'
import type { Turn } from './types'

const memory = () => {
  const data = new Map<string, string>()
  return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value) } }
}
const turn = (id: string, scenario: Turn['scenario']): Turn => ({ id, scenario, mode: 'demo', question: '测试问题', stage: 5, elapsed: 3, ...resultFor(scenario) })

describe('浏览器会话保存', () => {
  it('重新打开后恢复模式、选中会话、草稿、完整引用与分类记录', () => {
    const storage = memory(), history = openHistory(() => storage, 'c1'), state = initialState('c1')
    state.conversations[0].mode = 'demo'
    state.conversations[0].turns = [turn('knowledge', 'knowledge'), turn('business', 'business'), turn('award', 'award')]
    state.draft = '未发送的草稿'
    state.scenario = 'business'
    state.conversations.push({ id: 'c2', title: '真实会话', mode: 'live', turns: [] })
    expect(history.save(state)).toBe('saved')
    expect(openHistory(() => storage, 'unused').initial).toEqual(state)
  })
  it('所有未完成轮恢复为中断，保留部分正文，且仍能开始新请求', () => {
    const storage = memory(), state = initialState('c1')
    state.conversations[0].mode = 'demo'
    state.conversations[0].turns = ['running', 'streaming'].map((status, i) => ({ ...turn(String(i), 'knowledge'), status: status as Turn['status'], answer: '部分正文', citations: [] }))
    openHistory(() => storage, 'new').save(state)
    const restored = openHistory(() => storage, 'unused').initial
    expect(restored.conversations[0].turns.map(t => t.status)).toEqual(['interrupted', 'interrupted'])
    expect(restored.conversations[0].turns[1].answer).toBe('部分正文')
    const next = reducer(restored, { type: 'start', conversationId: 'c1', turn: { ...turn('next', 'general'), status: 'running' }, clearDraft: true })
    expect(next.conversations[0].turns).toHaveLength(3)
  })
  it('损坏、未知版本或错误嵌套数据不会崩溃，也不会自动覆盖原记录', () => {
    for (const raw of ['broken', JSON.stringify({ version: 999, state: initialState('c1') }), JSON.stringify({ version: 1, state: { ...initialState('c1'), conversations: [{ id: 'c1', mode: 'live', title: 'bad', turns: [{}] }] } })]) {
      const storage = memory(); storage.setItem(HISTORY_KEY, raw)
      const history = openHistory(() => storage, 'fresh')
      expect(history.status).toBe('invalid')
      expect(history.initial.activeId).toBe('fresh')
      expect(history.save(history.initial)).toBe('invalid')
      expect(storage.getItem(HISTORY_KEY)).toBe(raw)
    }
  })
  it('存储禁用或容量不足时提示失败，保留之前已保存的数据', () => {
    const denied = openHistory(() => { throw new Error('SecurityError') }, 'c1')
    expect(denied.status).toBe('unavailable')
    expect(denied.save(denied.initial)).toBe('unavailable')
    const storage = memory(), history = openHistory(() => storage, 'c1')
    history.save(history.initial)
    const before = storage.getItem(HISTORY_KEY)
    storage.setItem = () => { throw new Error('QuotaExceededError') }
    expect(history.save({ ...history.initial, draft: '新草稿' })).toBe('unavailable')
    expect(storage.getItem(HISTORY_KEY)).toBe(before)
  })
  it('旧标签页不能覆盖另一个标签页更新或清空后的记录', () => {
    const storage = memory(), first = openHistory(() => storage, 'c1')
    first.save(first.initial)
    const second = openHistory(() => storage, 'c2')
    second.save({ ...second.initial, draft: '新标签页草稿' })
    expect(first.changedElsewhere()).toBe(true)
    expect(first.save({ ...first.initial, draft: '旧页面草稿' })).toBe('conflict')
    expect(openHistory(() => storage, 'check').initial.draft).toBe('新标签页草稿')
    second.save(initialState('cleared'), true)
    expect(first.save(first.initial)).toBe('conflict')
    expect(openHistory(() => storage, 'check').initial.activeId).toBe('cleared')
  })
  it('清空只替换本应用记录；重置后迟到事件不能恢复被清空的轮次', () => {
    const storage = memory(), history = openHistory(() => storage, 'c1'), state = initialState('c1')
    storage.setItem('other-application', '必须保留')
    state.conversations[0].mode = 'demo'
    state.conversations[0].turns = [{ ...turn('old', 'knowledge'), status: 'streaming' }]
    history.save(state)
    const fresh = initialState('fresh')
    expect(history.save(fresh, true)).toBe('saved')
    expect(openHistory(() => storage, 'check').initial.conversations[0].turns).toEqual([])
    expect(storage.getItem('other-application')).toBe('必须保留')
    let next = reducer(state, { type: 'reset', state: fresh })
    next = reducer(next, { type: 'finish', conversationId: 'c1', runId: 'old', result: resultFor('knowledge'), elapsed: 9 })
    expect(next).toEqual(fresh)
  })
  it('经显式清空可从损坏数据恢复；已停止和业务非成功状态不会改变', () => {
    const storage = memory(); storage.setItem(HISTORY_KEY, 'broken')
    const history = openHistory(() => storage, 'fresh')
    expect(history.save(history.initial, true)).toBe('saved')
    const state = history.initial
    state.conversations[0].mode = 'demo'
    state.conversations[0].turns = ['cancelled', 'failed', 'partial', 'clarify', 'insufficient_evidence'].map((status, i) => ({ ...turn(String(i), 'general'), status: status as Turn['status'] }))
    history.save(state)
    expect(openHistory(() => storage, 'check').initial).toEqual(state)
  })
})
