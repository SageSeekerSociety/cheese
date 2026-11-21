// A stand-in for a language server's stdio, for tests of the editor's LSP client:
// it reads and writes real LSP base-protocol bytes (`Content-Length` headers and a
// UTF-8 JSON body), answers requests from `handlers`, and records what it was sent.
import type { ByteTransport } from '../lib/lsp/client'

export interface RpcMessage {
  jsonrpc: '2.0'
  id?: number | string
  method?: string
  params?: unknown
  result?: unknown
  error?: unknown
}

const encoder = new TextEncoder()
const decoder = new TextDecoder()

/** One LSP message as the bytes on the wire. */
export function frame(message: object): Uint8Array {
  const body = encoder.encode(JSON.stringify(message))
  const head = encoder.encode(`Content-Length: ${body.length}\r\n\r\n`)
  const bytes = new Uint8Array(head.length + body.length)
  bytes.set(head)
  bytes.set(body, head.length)
  return bytes
}

export class FakeLanguageServer {
  /** Every message the client sent, in order. */
  readonly received: RpcMessage[] = []
  /** Answers by method; what a handler returns is the result. */
  handlers: Record<string, (params: unknown) => unknown> = {
    initialize: () => ({ capabilities: this.capabilities }),
    shutdown: () => null,
  }
  capabilities: Record<string, unknown> = {
    textDocumentSync: 1,
    completionProvider: { triggerCharacters: ['.'], resolveProvider: true },
    hoverProvider: true,
    definitionProvider: true,
    referencesProvider: true,
  }
  closed = false
  private pending = new Uint8Array(0)
  private dataListeners = new Set<(bytes: Uint8Array) => void>()
  private closeListeners = new Set<() => void>()
  private waiters: { test: (m: RpcMessage) => boolean; resolve: (m: RpcMessage) => void }[] = []

  /** The client's end. */
  readonly transport: ByteTransport = {
    send: (bytes) => this.read(bytes),
    onData: (listener) => {
      this.dataListeners.add(listener)
      return () => this.dataListeners.delete(listener)
    },
    onClose: (listener) => {
      this.closeListeners.add(listener)
      return () => this.closeListeners.delete(listener)
    },
    close: () => this.hangUp(),
  }

  /** Raw bytes to the client, exactly as given. */
  write(bytes: Uint8Array): void {
    for (const listener of [...this.dataListeners]) listener(bytes)
  }

  notify(method: string, params: unknown): void {
    this.write(frame({ jsonrpc: '2.0', method, params }))
  }

  /** The server goes away. */
  hangUp(): void {
    if (this.closed) return
    this.closed = true
    for (const listener of [...this.closeListeners]) listener()
  }

  /** Messages the client sent with this method. */
  sent(method: string): RpcMessage[] {
    return this.received.filter((m) => m.method === method)
  }

  /** The next message with this method (or one already received after `after` of them). */
  next(method: string, after = this.sent(method).length): Promise<RpcMessage> {
    const already = this.sent(method)
    if (already.length > after) return Promise.resolve(already[after])
    return new Promise((resolve) => this.waiters.push({ test: (m) => m.method === method, resolve }))
  }

  private read(bytes: Uint8Array) {
    const joined = new Uint8Array(this.pending.length + bytes.length)
    joined.set(this.pending)
    joined.set(bytes, this.pending.length)
    this.pending = joined
    for (;;) {
      const text = decoder.decode(this.pending.subarray(0, Math.min(this.pending.length, 256)))
      const end = text.indexOf('\r\n\r\n')
      if (end < 0) return
      const length = Number(/Content-Length: *(\d+)/i.exec(text.slice(0, end))?.[1])
      const start = end + 4
      if (this.pending.length < start + length) return
      const message = JSON.parse(decoder.decode(this.pending.subarray(start, start + length))) as RpcMessage
      this.pending = this.pending.slice(start + length)
      this.receive(message)
    }
  }

  private receive(message: RpcMessage) {
    this.received.push(message)
    for (const waiter of [...this.waiters]) {
      if (!waiter.test(message)) continue
      this.waiters.splice(this.waiters.indexOf(waiter), 1)
      waiter.resolve(message)
    }
    if (message.method && message.id !== undefined) {
      const handler = this.handlers[message.method]
      const reply = handler
        ? { jsonrpc: '2.0', id: message.id, result: handler(message.params) ?? null }
        : { jsonrpc: '2.0', id: message.id, error: { code: -32601, message: 'not here' } }
      // A server answers later, not inside the client's write.
      queueMicrotask(() => this.write(frame(reply)))
    }
  }
}
