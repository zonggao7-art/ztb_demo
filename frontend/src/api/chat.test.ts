import { describe, expect, it, vi } from 'vitest'
import { SSEDecoder, readEvents } from './sse'
import { requestChat } from './chat'
import { adaptFinal, publishedText } from './results'

const encoder = new TextEncoder()
const frame = (type: string, payload: object = {}, id = 'server-1') => `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify({ type, request_id: id, payload, ts: 1 })}\n\n`
const final = (data: object = {}, status = 'complete') => ({ answer: '已发布正文', business_result: { answer: '已发布正文', execution_status: status, data } })
const stream = (text: string) => new ReadableStream<Uint8Array>({ start(c) { c.enqueue(encoder.encode(text)); c.close() } })
const callbacks = () => ({ bind: vi.fn(), stage: vi.fn(), token: vi.fn() })
const fetchText = (text: string) => vi.fn<typeof fetch>(async () => new Response(stream(text), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } }))
const action = (step_id: string, tool: string, rest = {}) => ({ step_id, tool, ok: true, args: {}, outcome: 'tool_result', ...rest })

describe('网络分片和终态', () => {
  it('逐字节接收汉字、CRLF、注释和多帧，不丢字或重复', () => {
    const decoder = new SSEDecoder(), events = []
    const bytes = encoder.encode((': keepalive\n\n' + frame('token', { delta: '汉字🙂' }) + frame('heartbeat')).replaceAll('\n', '\r\n'))
    for (const byte of bytes) events.push(...decoder.push(new Uint8Array([byte])))
    events.push(...decoder.push())
    expect(events.map(e => e.type)).toEqual(['token', 'heartbeat'])
    expect(events[0].payload.delta).toBe('汉字🙂')
  })
  it('支持多行 data 与单独 CR 分隔', () => {
    const decoder = new SSEDecoder()
    const raw = 'id: r\revent: token\rdata: {"type":"token","request_id":"r",\rdata: "payload":{"delta":"中文"},"ts":1}\r\r'
    const events = [...decoder.push(encoder.encode(raw)), ...decoder.push()]
    expect(events[0].payload.delta).toBe('中文')
  })
  it('拒绝截断、错误 JSON 和不一致的信封标识', () => {
    const decoder = new SSEDecoder()
    decoder.push(encoder.encode(frame('token').slice(0, -1)))
    expect(() => decoder.push()).toThrow()
    expect(() => new SSEDecoder().push(encoder.encode('data: broken\n\n'))).toThrow()
    expect(() => new SSEDecoder().push(encoder.encode(frame('token').replace('id: server-1', 'id: other')))).toThrow()
  })
  it('正文逐步回调，忽略其他请求；只有 final 发布业务分类', async () => {
    const cb = callbacks(), fetcher = fetchText(frame('meta', { mode: 'unified' }) + frame('token', { delta: '错轮' }, 'other') + frame('token', { delta: '已发布' }) + frame('table', { records: [{ untrusted: true }] }) + frame('final', final({}, 'clarify')))
    const result = await requestChat('问题', 'web-c1', new AbortController().signal, cb, fetcher)
    expect(cb.bind).toHaveBeenCalledWith('server-1')
    expect(cb.token.mock.calls).toEqual([['已发布']])
    expect(result).toMatchObject({ status: 'clarify', answer: '已发布正文', groups: [] })
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ question: '问题', thread_id: 'web-c1', deadline_s: 90 })
  })
  it('EOF 不能当作成功，错误事件也不能伪装成业务完成', async () => {
    for (const [tail, kind] of [[frame('token', { delta: '部分' }), 'incomplete'], [frame('error', { message: '内部信息不可展示' }), 'backend'], [frame('cancelled'), 'cancelled']]) {
      await expect(requestChat('问题', 'c', new AbortController().signal, callbacks(), fetchText(frame('meta', { mode: 'unified' }) + tail))).rejects.toMatchObject({ kind })
    }
  })
  it('拒绝未支持模式、非 SSE 响应和 HTTP 失败', async () => {
    await expect(requestChat('q', 'c', new AbortController().signal, callbacks(), fetchText(frame('meta', { mode: 'legacy' })))).rejects.toMatchObject({ kind: 'protocol' })
    for (const [status, kind] of [[200, 'protocol'], [422, 'http'], [503, 'http']] as const) {
      await expect(requestChat('q', 'c', new AbortController().signal, callbacks(), async () => new Response('internal detail', { status }))).rejects.toMatchObject({ kind })
    }
  })
  it('停止时解除悬挂读取并释放流，final 后也关闭底层读取', async () => {
    const cancel = vi.fn(), controller = new AbortController()
    const pending = new ReadableStream<Uint8Array>({ cancel })
    const reader = readEvents(pending, controller.signal)
    const result = reader.next()
    controller.abort()
    await expect(result).rejects.toThrow()
    expect(cancel).toHaveBeenCalledOnce()
    expect(pending.locked).toBe(false)
    const terminalCancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(encoder.encode(frame('meta', { mode: 'unified' }) + frame('final', final()))) }, cancel: terminalCancel })
    await requestChat('q', 'c', new AbortController().signal, callbacks(), async () => new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }))
    expect(terminalCancel).toHaveBeenCalledOnce()
  })
})

describe('最终业务结果的可信分类', () => {
  it('按 step_id 对应工具分组，记录顺序或相似字段不能改变类型', () => {
    const data = {
      actions: [action('s1', 'query_company_registration'), action('s2', 'query_company_business_scope'), action('s3', 'query_company_penalty'), action('s4', 'query_project_award'), action('s5', 'query_company_award_history')],
      records: [
        { step_id: 's4', record_ref: 'same', winning_amount: 0, project_number: 'P1' },
        { step_id: 's1', record_ref: 'same', company_name: '企业', legal_person: null, project_number: '不能据此误判为中标' },
        { step_id: 's3', record_ref: 'p', penalty_result: '长段落' },
        { step_id: 's2', record_ref: 'scope', business_scope: '长文本\n第二段' },
        { step_id: 's5', record_ref: 'a', winning_amount: '123.40' },
      ],
    }
    const result = adaptFinal(final(data)), groups = result.groups!
    expect(groups.map(g => g.kind)).toEqual(['company', 'scope', 'penalty', 'award', 'award'])
    expect(groups[0].rows[0].values).toMatchObject({ company_name: '企业', legal_person: '未收录' })
    expect(groups[0].rows[0].values).not.toHaveProperty('project_number')
    expect(groups[3].rows[0].values.winning_amount).toBe('0')
    expect(result.notice).toBeUndefined()
  })
  it('精确空结果、宽泛空结果、失败与拒绝各自保留语义', () => {
    const actions = [action('s1', 'query_company_penalty', { empty: true, exact_scope: true }), action('s2', 'query_company_penalty', { empty: true }), action('s3', 'query_project_award', { ok: false }), action('s4', 'query_project_award', { ok: false, outcome: 'policy_rejection' })]
    expect(adaptFinal(final({ actions })).groups?.map(g => g.outcome)).toEqual(['empty', 'unavailable', 'failed', 'rejected'])
  })
  it('关联缺失或不唯一时不猜测类型，并明确提示', () => {
    const result = adaptFinal(final({ actions: [action('s1', 'query_project_award'), action('s1', 'query_company_penalty')], records: [{ step_id: 's1', record_ref: 'a' }, { step_id: 'orphan', company_name: '孤立' }] }))
    expect(result.groups).toEqual([])
    expect(result.notice).toContain('关联缺失或不唯一')
    const duplicates = adaptFinal(final({ actions: [action('s1', 'query_project_award')], records: [{ step_id: 's1', record_ref: 'a' }, { step_id: 's1', record_ref: 'a' }] }))
    expect(duplicates.groups![0].rows).toEqual([])
    expect(duplicates.groups![0].outcome).toBe('unavailable')
  })
  it('保留真实引用与来源标识，损坏引用不能标成完成', () => {
    const citation = { context_index: 1, doc_name: '法规', chapter: '条款', text: '原文', chunk_uid: 'uid', chunk_id: 'id' }
    expect(adaptFinal(final({ citations: [citation] })).citations[0]).toMatchObject({ index: 1, title: '法规', text: '原文', chunkUid: 'uid' })
    expect(() => adaptFinal(final({ citations: [citation, citation] }))).toThrow()
  })
  it('业务证据不足或不支持不升级为完成；两份最终正文必须一致', () => {
    for (const status of ['insufficient_evidence', 'unsupported', 'partial']) expect(adaptFinal(final({}, status)).status).toBe(status)
    expect(() => adaptFinal({ ...final(), answer: '冲突' })).toThrow()
  })
  it('转义仅还原成文本，不递归解码；保留原始换行和引用', () => {
    expect(publishedText('A\\_B &lt;script&gt;\n【来源1】 &amp;lt;')).toBe('A_B <script>\n【来源1】 &lt;')
  })
})
