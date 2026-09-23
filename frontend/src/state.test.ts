import { describe, expect, it } from 'vitest'
import { initialState, reducer } from './state'
import type { Turn } from './types'

const turn: Turn = { id: 'r1', question: '测试问题', scenario: 'knowledge', status: 'running', stage: 0, answer: '', citations: [], records: [], elapsed: 0 }
const target = { conversationId: 'c1', runId: 'r1' }
const running = () => reducer(initialState('c1'), { type: 'start', conversationId: 'c1', turn, clearDraft: true })
const current = (state: ReturnType<typeof running>) => state.conversations[0].turns[0]
describe('一次演示请求的完整性与隔离', () => {
  it('停止后保留正文，丢弃迟到的片段和最终结果', () => {
    let state = reducer(running(), { type: 'token', ...target, delta: '已收到', elapsed: 1 })
    state = reducer(state, { type: 'stop', ...target, status: 'cancelled', notice: '已停止', elapsed: 2 })
    state = reducer(state, { type: 'token', ...target, delta: '迟到内容', elapsed: 3 })
    state = reducer(state, { type: 'finish', ...target, result: { answer: '不该成功', citations: [], records: [], status: 'complete' }, elapsed: 4 })
    expect(current(state).status).toBe('cancelled')
    expect(current(state).answer).toBe('已收到')
  })
  it('完整结果校准正文，不重复追加；业务状态可以需要补充条件', () => {
    const partial = reducer(running(), { type: 'token', ...target, delta: '请补充', elapsed: 1 })
    const state = reducer(partial, { type: 'finish', ...target, result: { answer: '请补充项目编号', citations: [], records: [], status: 'clarify' }, elapsed: 2 })
    expect(current(state).answer).toBe('请补充项目编号')
    expect(current(state).status).toBe('clarify')
  })
  it('其他会话或请求的片段不能混入当前请求', () => {
    let state = reducer(running(), { type: 'token', conversationId: 'other', runId: 'r1', delta: '错误', elapsed: 1 })
    state = reducer(state, { type: 'token', conversationId: 'c1', runId: 'old', delta: '错误', elapsed: 1 })
    expect(current(state).answer).toBe('')
  })
  it('运行期间拒绝重复发送', () => {
    const state = reducer(running(), { type: 'start', conversationId: 'c1', turn: { ...turn, id: 'r2' }, clearDraft: true })
    expect(state.conversations[0].turns).toHaveLength(1)
  })
  it('网络中断不能升级为完成，重试保留用户正在编辑的草稿', () => {
    let state = reducer(running(), { type: 'stop', ...target, status: 'interrupted', notice: '中断', elapsed: 2 })
    state = reducer(state, { type: 'draft', value: '下一条草稿' })
    state = reducer(state, { type: 'start', conversationId: 'c1', turn: { ...turn, id: 'retry' }, clearDraft: false })
    expect(state.draft).toBe('下一条草稿')
    expect(state.conversations[0].turns.map(t => t.status)).toEqual(['interrupted', 'running'])
  })
})
