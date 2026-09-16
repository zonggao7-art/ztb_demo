export interface StreamEvent { type: string; request_id: string; payload: Record<string, unknown>; ts: number }
export class StreamProtocolError extends Error {}
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// 每次网络 read 可能是半帧、多个帧，甚至半个汉字。先解码，再按空行分帧。
export class SSEDecoder {
  private decoder = new TextDecoder('utf-8', { fatal: true })
  private buffer = ''
  private lines: string[] = []
  private frameSize = 0
  push(bytes?: Uint8Array): StreamEvent[] {
    this.buffer += bytes ? this.decoder.decode(bytes, { stream: true }) : this.decoder.decode()
    const events: StreamEvent[] = []
    while (true) {
      const pos = this.buffer.search(/[\r\n]/)
      if (pos < 0 || (bytes && this.buffer[pos] === '\r' && pos === this.buffer.length - 1)) break
      const line = this.buffer.slice(0, pos)
      const length = this.buffer[pos] === '\r' && this.buffer[pos + 1] === '\n' ? 2 : 1
      this.buffer = this.buffer.slice(pos + length)
      if (!line) {
        const event = this.parseFrame()
        if (event) events.push(event)
        this.lines = []; this.frameSize = 0
      } else {
        this.lines.push(line); this.frameSize += line.length
      }
      if (this.frameSize > 4_000_000) throw new StreamProtocolError('事件过大')
    }
    if (this.buffer.length + this.frameSize > 4_000_000) throw new StreamProtocolError('事件过大')
    if (!bytes && (this.buffer.trim() || this.lines.some(line => line.startsWith('data:')))) {
      throw new StreamProtocolError('事件未完整接收')
    }
    return events
  }
  private parseFrame(): StreamEvent | null {
    let id: string | undefined, type: string | undefined
    const data: string[] = []
    for (const line of this.lines) {
      if (line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const key = colon < 0 ? line : line.slice(0, colon)
      const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
      if (key === 'id') id = value
      if (key === 'event') type = value
      if (key === 'data') data.push(value)
    }
    if (!data.length) return null
    let event: unknown
    try { event = JSON.parse(data.join('\n')) } catch { throw new StreamProtocolError('事件不是有效 JSON') }
    if (!isObject(event) || typeof event.type !== 'string' || typeof event.request_id !== 'string' || !event.request_id
      || !isObject(event.payload) || typeof event.ts !== 'number' || !Number.isFinite(event.ts)
      || event.type !== type || event.request_id !== id) throw new StreamProtocolError('事件格式不匹配')
    return event as unknown as StreamEvent
  }
}

export async function* readEvents(body: ReadableStream<Uint8Array>, signal: AbortSignal): AsyncGenerator<StreamEvent> {
  const reader = body.getReader(), decoder = new SSEDecoder()
  const onAbort = () => { void reader.cancel().catch(() => {}) }
  signal.addEventListener('abort', onAbort, { once: true })
  try {
    while (true) {
      signal.throwIfAborted()
      const { done, value } = await reader.read()
      signal.throwIfAborted()
      for (const event of decoder.push(done ? undefined : value)) yield event
      if (done) return
    }
  } finally {
    signal.removeEventListener('abort', onAbort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
