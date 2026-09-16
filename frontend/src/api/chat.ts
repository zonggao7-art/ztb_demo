import { adaptFinal, toolTitle } from './results'
import { readEvents, StreamProtocolError } from './sse'
import type { Result } from '../types'

export class ChatFailure extends Error {
  constructor(public kind: 'http' | 'backend' | 'cancelled' | 'incomplete' | 'protocol' | 'network', message: string) { super(message) }
}
interface Callbacks { bind: (id: string) => void; stage: (label: string) => void; token: (delta: string) => void }
export const stageText = (payload: Record<string, unknown>): string => {
  if (payload.stage === 'tool_call') return payload.status === 'running' ? `正在查询${toolTitle(payload.tool)}`
    : payload.ok === false ? `${toolTitle(payload.tool)}未成功返回` : `${toolTitle(payload.tool)}查询已返回`
  const labels: Record<string, string> = { agent_start: '正在处理问题', agent_finish: '查询步骤已结束，等待校验', validation_start: '正在校验结果', validation_done: '校验结束，等待最终结果', execution_degraded: '部分步骤未完成，正在整理可用结果', request_failed: '本轮未通过完整核验', model_repair: '正在调整查询请求', input_limit: '请求内容超过处理上限' }
  return typeof payload.stage === 'string' && Object.hasOwn(labels, payload.stage) ? labels[payload.stage] : '正在处理后续步骤'
}

export async function requestChat(question: string, threadId: string, signal: AbortSignal, callbacks: Callbacks, fetcher: typeof fetch = fetch): Promise<Result> {
  try {
    const response = await fetcher('/api/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question, thread_id: threadId, deadline_s: 90 }), signal,
    })
    if (!response.ok) {
      await response.body?.cancel().catch(() => {})
      throw new ChatFailure('http', response.status === 422 ? '问题未通过接口校验，请检查输入后重试。' : `请求未能开始（HTTP ${response.status}），请检查后端服务后重试。`)
    }
    if (!response.headers.get('content-type')?.toLowerCase().startsWith('text/event-stream') || !response.body) {
      await response.body?.cancel().catch(() => {})
      throw new ChatFailure('protocol', '接口没有返回预期的事件流，请检查服务配置。')
    }
    let serverId: string | undefined
    for await (const event of readEvents(response.body, signal)) {
      if (!serverId) {
        if (event.type === 'heartbeat') continue
        if (event.type !== 'meta' || event.payload.mode !== 'unified') throw new StreamProtocolError('当前前端仅支持 unified 事件契约')
        serverId = event.request_id; callbacks.bind(serverId); callbacks.stage('请求已接收，正在处理')
        continue
      }
      if (event.request_id !== serverId) continue
      if (event.type === 'stage') callbacks.stage(stageText(event.payload))
      else if (event.type === 'token') {
        if (typeof event.payload.delta !== 'string') throw new StreamProtocolError('正文片段格式不匹配')
        callbacks.token(event.payload.delta)
      } else if (event.type === 'final') return adaptFinal(event.payload)
      else if (event.type === 'error') throw new ChatFailure('backend', '后端未完成本次请求，请重试。当前接口未必能区分具体的超时或服务故障。')
      else if (event.type === 'cancelled') throw new ChatFailure('cancelled', '后端已取消本轮请求。')
      // table/citations 暂不展示：以 final 中的已发布数据及 actions 完成分类。
      // 心跳、未知非终态不作为正文，不自动升级为完成。
    }
    throw new ChatFailure('incomplete', '连接已结束，但未收到完整的最终结果。已有正文仅作保留，请重试。')
  } catch (error) {
    if (signal.aborted) throw error
    if (error instanceof ChatFailure) throw error
    if (error instanceof StreamProtocolError) throw new ChatFailure('protocol', '接收到的结果格式不完整，未将本轮标记为完成。请重试。')
    throw new ChatFailure('network', '无法连接或连接已中断，请检查网络及后端服务后重试。')
  }
}
