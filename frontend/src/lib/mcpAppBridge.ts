// MCP Apps (`io.modelcontextprotocol/ui`): the host half of the JSON-RPC bridge
// a third-party View speaks over postMessage.
//
// This is NOT the room artifacts' bridge. Those documents are served with cheese's
// own runtime injected into them (`preview_runtime.py`, `cheese-preview-runtime`),
// and the host knows the page because it wrote it. A third party's View is opaque:
// it is served on its own origin, nothing of ours is injected, and it calls the
// host itself with plain JSON-RPC 2.0. So it gets its own bridge and its own
// sandbox (``MCP_APP_SANDBOX``) — never the room's ``allow-same-origin``.
//
// Scope this round: the handshake is answered so a View can render, and `ui/message`
// — the user hitting submit in the View's own form — is decoded to text and handed to
// the host through `handlers.onMessage`. Every other call still gets a JSON-RPC
// "method not found" rather than hanging; `tools/call` is a later step.

export const MCP_APP_MIME = 'text/html;profile=mcp-app'
// The iframe a third party's View runs in: scripts and forms only. It must never
// carry `allow-same-origin` — with `allow-scripts` that would hand a document we
// did not write the content origin's storage. The host's CSP strips it too; the
// attribute has to agree.
export const MCP_APP_SANDBOX = 'allow-scripts allow-forms'
export const MCP_APP_PROTOCOL_VERSION = '2026-01-26'

interface RpcMessage {
  jsonrpc: '2.0'
  id?: string | number
  method: string
  params?: unknown
}

/** A well-formed JSON-RPC message from the View, or null. */
function rpc(data: unknown): RpcMessage | null {
  if (!data || typeof data !== 'object') return null
  const message = data as Record<string, unknown>
  if (message.jsonrpc !== '2.0' || typeof message.method !== 'string') return null
  return message as unknown as RpcMessage
}

export interface McpAppHostInfo {
  name: string
  version: string
}

/** A `ui/message` the View sent: the text of its form, and who it says it came from. */
export interface McpAppMessage {
  role: string
  text: string
}

export interface McpAppHandlers {
  /** A decodable `ui/message` — the View's form was submitted. */
  onMessage?: (message: McpAppMessage) => void
}

/** The text of a `ui/message`: every `type==='text'` part joined and trimmed. An
 *  unrecognizable shape or empty text yields null — such a call is still acked (the
 *  View must not hang), it just never becomes a message. */
function messageText(params: unknown): string | null {
  if (!params || typeof params !== 'object') return null
  const content = (params as Record<string, unknown>).content
  if (!Array.isArray(content)) return null
  const text = content
    .map((part) =>
      part && typeof part === 'object' && (part as Record<string, unknown>).type === 'text'
        ? (part as Record<string, unknown>).text
        : ''
    )
    .filter((value): value is string => typeof value === 'string')
    .join('')
    .trim()
  return text || null
}

/** Who the View said the message is from; the protocol's only sensible default. */
function messageRole(params: unknown): string {
  if (params && typeof params === 'object') {
    const role = (params as Record<string, unknown>).role
    if (typeof role === 'string' && role) return role
  }
  return 'user'
}

export interface McpAppBridge {
  /** Handle one message event; true when it was this bridge's. */
  handle(event: MessageEvent): boolean
  /** Host → View notification (tool input/result, theme). */
  notify(method: string, params?: unknown): void
}

/**
 * The host side, bound to one View's window. The View's origin is opaque, so
 * replies go out with `'*'` as the target — the sender check is the frame identity,
 * not an origin string.
 */
export function createMcpAppBridge(frame: Window, host: McpAppHostInfo, handlers: McpAppHandlers = {}): McpAppBridge {
  function reply(id: string | number, result?: unknown, error?: unknown) {
    frame.postMessage({ jsonrpc: '2.0', id, ...(error === undefined ? { result } : { error }) }, '*')
  }
  function handle(event: MessageEvent): boolean {
    if (event.source !== frame) return false
    const message = rpc(event.data)
    if (!message) return false
    if (message.id === undefined) return true // a notification: no answer owed
    if (message.method === 'ui/initialize') {
      reply(message.id, {
        protocolVersion: MCP_APP_PROTOCOL_VERSION,
        hostCapabilities: {},
        hostInfo: host,
      })
    } else if (message.method === 'ui/message') {
      // Decoded text goes out through the handler; the ack goes back either way so a
      // View whose message we could not read is not left waiting on a promise.
      const text = messageText(message.params)
      if (text) handlers.onMessage?.({ role: messageRole(message.params), text })
      reply(message.id, {})
    } else {
      reply(message.id, undefined, { code: -32601, message: `unsupported method: ${message.method}` })
    }
    return true
  }
  function notify(method: string, params?: unknown) {
    frame.postMessage({ jsonrpc: '2.0', method, ...(params === undefined ? {} : { params }) }, '*')
  }
  return { handle, notify }
}
