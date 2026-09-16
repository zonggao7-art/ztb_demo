import type { Conversation, Mode, Result, RunStatus, Scenario, Turn } from './types'

export interface State { conversations: Conversation[]; activeId: string; draft: string; scenario: Scenario }
type Target = { conversationId: string; runId: string }
export type Action =
  | { type: 'reset'; state: State }
  | { type: 'draft'; value: string }
  | { type: 'scenario'; value: Scenario }
  | { type: 'new'; id: string; mode?: Mode }
  | { type: 'select'; id: string }
  | { type: 'start'; conversationId: string; turn: Turn; clearDraft: boolean }
  | ({ type: 'stage'; stage: number; elapsed: number } & Target)
  | ({ type: 'live-stage'; label: string; elapsed: number } & Target)
  | ({ type: 'bind'; serverRequestId: string } & Target)
  | ({ type: 'tick'; elapsed: number } & Target)
  | ({ type: 'token'; delta: string; elapsed: number } & Target)
  | ({ type: 'finish'; result: Result; elapsed: number } & Target)
  | ({ type: 'stop'; status: 'cancelled' | 'failed' | 'interrupted'; notice: string; elapsed: number } & Target)

export const isBusy = (status: RunStatus) => status === 'running' || status === 'streaming'
export const initialState = (id: string): State => ({
  conversations: [{ id, title: '新的会话', turns: [], mode: 'live' }], activeId: id, draft: '', scenario: 'knowledge',
})

export function reducer(state: State, action: Action): State {
  if (action.type === 'reset') return action.state
  if (action.type === 'draft') return { ...state, draft: action.value }
  if (action.type === 'scenario') return { ...state, scenario: action.value }
  if (action.type === 'new') return { ...state, activeId: action.id, conversations: [{ id: action.id, title: '新的会话', turns: [], mode: action.mode }, ...state.conversations] }
  if (action.type === 'select') return state.conversations.some(c => c.id === action.id) ? { ...state, activeId: action.id } : state
  if (action.type === 'start') {
    if (state.conversations.some(c => c.turns.some(t => isBusy(t.status)))) return state
    return { ...state, draft: action.clearDraft ? '' : state.draft, conversations: state.conversations.map(c => c.id !== action.conversationId ? c : {
      ...c, title: c.turns.length ? c.title : action.turn.question, turns: [...c.turns, action.turn],
    }) }
  }
  return { ...state, conversations: state.conversations.map(c => c.id !== action.conversationId ? c : {
    ...c, turns: c.turns.map(t => {
      // 已停止、失败或结束的请求忽略迟到事件；其他请求也不能串入本轮。
      if (t.id !== action.runId || !isBusy(t.status)) return t
      if (action.type === 'bind') return { ...t, serverRequestId: action.serverRequestId }
      if (action.type === 'tick') return { ...t, elapsed: action.elapsed }
      if (action.type === 'live-stage') return { ...t, stageLabel: action.label, elapsed: action.elapsed }
      if (action.type === 'stage') return { ...t, stage: action.stage, elapsed: action.elapsed }
      if (action.type === 'token') return { ...t, answer: t.answer + action.delta, status: 'streaming', elapsed: action.elapsed }
      if (action.type === 'finish') return { ...t, ...action.result, elapsed: action.elapsed }
      return { ...t, status: action.status, notice: action.notice, elapsed: action.elapsed }
    }),
  }) }
}
