import { initialState, isBusy } from './state'
import type { State } from './state'
import type { Conversation, Turn } from './types'

export const HISTORY_KEY = 'bidding-assistant.history.v1'
type StoragePort = Pick<Storage, 'getItem' | 'setItem'>
export type SaveStatus = 'saved' | 'unavailable' | 'conflict' | 'invalid'
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const string = (v: unknown): v is string => typeof v === 'string'
const optionalString = (v: unknown) => v === undefined || string(v)
const mode = (v: unknown) => v === 'live' || v === 'demo'
const scenario = (v: unknown) => ['knowledge', 'award', 'business', 'general', 'clarify', 'partial', 'error', 'timeout', 'disconnect'].includes(String(v))
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0
const uniqueIds = (rows: { id: string }[]) => new Set(rows.map(r => r.id)).size === rows.length

function validTurn(v: unknown): v is Turn {
  if (!object(v) || !string(v.id) || !v.id || !string(v.question) || !scenario(v.scenario) || !mode(v.mode)
    || !['running', 'streaming', 'complete', 'clarify', 'partial', 'unsupported', 'insufficient_evidence', 'failed', 'cancelled', 'interrupted'].includes(String(v.status))
    || !finite(v.stage) || !finite(v.elapsed) || !string(v.answer) || !optionalString(v.notice)
    || !optionalString(v.stageLabel) || !optionalString(v.serverRequestId)) return false
  if (!Array.isArray(v.citations) || !v.citations.every(c => object(c) && Number.isInteger(c.index) && Number(c.index) > 0
    && string(c.title) && string(c.chapter) && string(c.text) && optionalString(c.chunkId) && optionalString(c.chunkUid))
    || new Set(v.citations.map(c => c.index)).size !== v.citations.length) return false
  if (!Array.isArray(v.records) || !v.records.every(r => object(r) && ['id', 'name', 'supplier', 'amount', 'date'].every(k => string(r[k])))
    || !uniqueIds(v.records)) return false
  if (v.groups !== undefined && (!Array.isArray(v.groups) || !v.groups.every(g => object(g)
    && string(g.id) && string(g.title) && string(g.scope) && ['company', 'scope', 'penalty', 'award'].includes(String(g.kind))
    && ['records', 'empty', 'failed', 'unavailable', 'rejected'].includes(String(g.outcome))
    && Array.isArray(g.fields) && g.fields.every(f => object(f) && string(f.key) && string(f.label))
    && Array.isArray(g.rows) && g.rows.every(r => object(r) && string(r.id) && object(r.values) && Object.values(r.values).every(string))
    && uniqueIds(g.rows)) || !uniqueIds(v.groups))) return false
  return true
}

function decode(raw: string): State {
  const value: unknown = JSON.parse(raw)
  if (!object(value) || value.version !== 1 || !object(value.state)) throw new Error('history version')
  const s = value.state
  if (!string(s.activeId) || !string(s.draft) || !scenario(s.scenario) || !Array.isArray(s.conversations) || !s.conversations.length) throw new Error('history shape')
  const conversations: Conversation[] = s.conversations.map(c => {
    if (!object(c) || !string(c.id) || !c.id || !string(c.title) || !mode(c.mode) || !Array.isArray(c.turns)
      || !c.turns.every(validTurn) || !uniqueIds(c.turns) || c.turns.some(t => t.mode !== c.mode)) throw new Error('conversation shape')
    return { id: c.id, title: c.title, mode: c.mode as Conversation['mode'], turns: c.turns.map(t => isBusy(t.status)
      ? { ...t, status: 'interrupted' as const, notice: '上次离开页面时，本轮尚未接收完成。已保留收到的内容，可手动重试。' }
      : t) }
  })
  if (!uniqueIds(conversations) || !conversations.some(c => c.id === s.activeId)) throw new Error('active conversation')
  return { conversations, activeId: s.activeId, draft: s.draft, scenario: s.scenario as State['scenario'] }
}

// 每次写入核对上次读到的版本，避免旧标签页覆盖新标签页刚保存的记录。
export function openHistory(getStorage: () => StoragePort, freshId: string) {
  let expected: string | null = null
  let status: SaveStatus = 'saved'
  let initial = initialState(freshId)
  try {
    expected = getStorage().getItem(HISTORY_KEY)
    if (expected !== null) initial = decode(expected)
  } catch {
    status = expected === null ? 'unavailable' : 'invalid'
  }
  return {
    initial,
    get status() { return status },
    save(state: State, replaceInvalid = false): SaveStatus {
      if (status === 'invalid' && !replaceInvalid) return status
      try {
        const storage = getStorage()
        if (storage.getItem(HISTORY_KEY) !== expected) return status = 'conflict'
        const next = JSON.stringify({ version: 1, state })
        if (next !== expected) storage.setItem(HISTORY_KEY, next)
        expected = next
        return status = 'saved'
      } catch { return status = 'unavailable' }
    },
    changedElsewhere() {
      try { return getStorage().getItem(HISTORY_KEY) !== expected } catch { return false }
    },
  }
}
