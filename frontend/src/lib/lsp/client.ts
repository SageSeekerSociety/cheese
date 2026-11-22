// A language server client over a stream of bytes: the editor's side of LSP.
//
// The bytes are the server's stdio as it is (LSP base protocol, `Content-Length`
// headers), carried by whatever `ByteTransport` the caller hands in — the task's
// language socket in the product, a fake server in tests. Framing and JSON-RPC
// (request ids, responses, cancellation) are `vscode-jsonrpc`'s: its stream reader
// takes bytes in whatever chunks they arrive and hands out whole messages.
//
// What this adds is the LSP conversation the editor needs: the handshake,
// keeping the server's copy of each open document equal to what the person sees
// (full text, edits debounced a little and always sent before a question about
// that document), and the five questions the editor asks. Nothing here knows
// Monaco or Vue.
import type { CancellationToken, Disposable, MessageConnection, RAL } from 'vscode-jsonrpc/browser'
import type {
  CompletionContext,
  CompletionItem,
  CompletionList,
  Definition,
  Hover,
  InitializeParams,
  InitializeResult,
  Location,
  LocationLink,
  Position,
  PublishDiagnosticsParams,
  ServerCapabilities,
} from 'vscode-languageserver-protocol'

import {
  createMessageConnection,
  ReadableStreamMessageReader,
  WriteableStreamMessageWriter,
} from 'vscode-jsonrpc/browser'

import { checkoutUri, fileUri } from './uri'

/** Bytes both ways, in chunks that mean nothing. */
export interface ByteTransport {
  send(bytes: Uint8Array): void
  /** Bytes from the server; returns a function that stops listening. */
  onData(listener: (bytes: Uint8Array) => void): () => void
  /** The transport is gone (either side closed it). */
  onClose(listener: () => void): () => void
  close(): void
}

/** How long edits settle before the server is told (ms). */
export const CHANGE_AFTER_MS = 150
/** How long a polite `shutdown` is waited for before the transport is simply closed (ms). */
const SHUTDOWN_WAIT_MS = 1000

type Listener<T> = (value: T) => void

function disposable(stop: () => void): Disposable {
  return { dispose: stop }
}

/** vscode-jsonrpc's stream reader and writer, over a `ByteTransport`. */
function streams(transport: ByteTransport): { readable: RAL.ReadableStream; writable: RAL.WritableStream } {
  const encoder = new TextEncoder()
  const closeListeners = new Set<() => void>()
  transport.onClose(() => {
    for (const listener of [...closeListeners]) listener()
  })
  const onClose = (listener: () => void) => {
    closeListeners.add(listener)
    return disposable(() => closeListeners.delete(listener))
  }
  const never = () => disposable(() => {})
  return {
    readable: {
      onData: (listener) => disposable(transport.onData(listener)),
      onClose,
      onError: never,
      onEnd: never,
    },
    writable: {
      onClose,
      onError: never,
      onEnd: never,
      write(data: Uint8Array | string) {
        transport.send(typeof data === 'string' ? encoder.encode(data) : data)
        return Promise.resolve()
      },
      end: () => transport.close(),
    },
  }
}

interface OpenDocument {
  languageId: string
  version: number
  /** Text not sent yet; null when the server has the latest. */
  pending: string | null
  timer: ReturnType<typeof setTimeout> | null
}

export class LspClient {
  /** What the server said it can do. */
  capabilities: ServerCapabilities = {}
  private readonly documents = new Map<string, OpenDocument>()
  private readonly diagnosticListeners = new Set<Listener<PublishDiagnosticsParams>>()
  private readonly closeListeners = new Set<() => void>()
  private closed = false

  private constructor(
    private readonly transport: ByteTransport,
    private readonly connection: MessageConnection,
    /** Where the checkout sits on the machine. */
    readonly root: string
  ) {}

  /** Opens the conversation with the server: `initialize`, then `initialized`. */
  static async start(transport: ByteTransport, root: string): Promise<LspClient> {
    const { readable, writable } = streams(transport)
    const connection = createMessageConnection(
      new ReadableStreamMessageReader(readable),
      new WriteableStreamMessageWriter(writable)
    )
    const client = new LspClient(transport, connection, root)
    connection.onNotification('textDocument/publishDiagnostics', (params: PublishDiagnosticsParams) => {
      for (const listener of client.diagnosticListeners) listener(params)
    })
    // What a server may ask of its client: answered so it does not wait on them.
    connection.onRequest('window/workDoneProgress/create', () => null)
    connection.onRequest('client/registerCapability', () => null)
    connection.onRequest('client/unregisterCapability', () => null)
    connection.onRequest('workspace/configuration', (params: { items?: unknown[] }) =>
      (params.items ?? []).map(() => null)
    )
    connection.onClose(() => client.ended())
    transport.onClose(() => client.ended())
    connection.listen()

    const rootUri = fileUri(root)
    const params: InitializeParams = {
      processId: null,
      clientInfo: { name: 'cheese' },
      rootUri,
      workspaceFolders: [{ uri: rootUri, name: root.split('/').filter(Boolean).pop() ?? root }],
      capabilities: {
        general: { positionEncodings: ['utf-16'] },
        textDocument: {
          synchronization: { dynamicRegistration: false, didSave: false, willSave: false },
          publishDiagnostics: { relatedInformation: false },
          completion: {
            contextSupport: true,
            completionItem: {
              snippetSupport: true,
              insertReplaceSupport: true,
              labelDetailsSupport: true,
              documentationFormat: ['markdown', 'plaintext'],
              resolveSupport: { properties: ['documentation', 'detail', 'additionalTextEdits'] },
            },
          },
          hover: { contentFormat: ['markdown', 'plaintext'] },
          definition: { linkSupport: true },
          references: {},
        },
      },
    }
    try {
      const result = await connection.sendRequest<InitializeResult>('initialize', params)
      client.capabilities = result.capabilities ?? {}
      await connection.sendNotification('initialized', {})
    } catch (error) {
      client.dispose()
      throw error
    }
    return client
  }

  /** The server's URI for a path of the checkout. */
  uriOf(path: string): string {
    return checkoutUri(this.root, path)
  }

  get isClosed(): boolean {
    return this.closed
  }

  onDiagnostics(listener: Listener<PublishDiagnosticsParams>): () => void {
    this.diagnosticListeners.add(listener)
    return () => this.diagnosticListeners.delete(listener)
  }

  /** The conversation ended: the server or its transport went away. */
  onClose(listener: () => void): () => void {
    this.closeListeners.add(listener)
    return () => this.closeListeners.delete(listener)
  }

  // ---------------------------------------------------------------- documents

  /** A document is open in an editor: the server gets its whole text. */
  open(uri: string, languageId: string, text: string): void {
    if (this.closed || this.documents.has(uri)) return
    this.documents.set(uri, { languageId, version: 1, pending: null, timer: null })
    this.notify('textDocument/didOpen', { textDocument: { uri, languageId, version: 1, text } })
  }

  /** The document's text now; told to the server once typing settles. */
  change(uri: string, text: string): void {
    const doc = this.documents.get(uri)
    if (!doc || this.closed) return
    doc.pending = text
    if (doc.timer) clearTimeout(doc.timer)
    doc.timer = setTimeout(() => this.flush(uri), CHANGE_AFTER_MS)
  }

  /** The editor closed the document. */
  close(uri: string): void {
    const doc = this.documents.get(uri)
    if (!doc) return
    if (doc.timer) clearTimeout(doc.timer)
    this.documents.delete(uri)
    this.notify('textDocument/didClose', { textDocument: { uri } })
  }

  /** Whether the server has this document open. */
  isOpen(uri: string): boolean {
    return this.documents.has(uri)
  }

  private flush(uri: string) {
    const doc = this.documents.get(uri)
    if (!doc) return
    if (doc.timer) clearTimeout(doc.timer)
    doc.timer = null
    if (doc.pending === null) return
    const text = doc.pending
    doc.pending = null
    doc.version += 1
    this.notify('textDocument/didChange', {
      textDocument: { uri, version: doc.version },
      contentChanges: [{ text }],
    })
  }

  // ---------------------------------------------------------------- questions

  completion(
    uri: string,
    position: Position,
    context?: CompletionContext,
    token?: CancellationToken
  ): Promise<CompletionItem[] | CompletionList | null> {
    return this.ask(uri, 'textDocument/completion', { textDocument: { uri }, position, context }, token)
  }

  resolveCompletion(item: CompletionItem, token?: CancellationToken): Promise<CompletionItem> {
    return token
      ? this.connection.sendRequest<CompletionItem>('completionItem/resolve', item, token)
      : this.connection.sendRequest<CompletionItem>('completionItem/resolve', item)
  }

  hover(uri: string, position: Position, token?: CancellationToken): Promise<Hover | null> {
    return this.ask(uri, 'textDocument/hover', { textDocument: { uri }, position }, token)
  }

  definition(uri: string, position: Position, token?: CancellationToken): Promise<Definition | LocationLink[] | null> {
    return this.ask(uri, 'textDocument/definition', { textDocument: { uri }, position }, token)
  }

  references(
    uri: string,
    position: Position,
    includeDeclaration: boolean,
    token?: CancellationToken
  ): Promise<Location[] | null> {
    return this.ask(
      uri,
      'textDocument/references',
      { textDocument: { uri }, position, context: { includeDeclaration } },
      token
    )
  }

  /** A question about a document, asked of the text the person sees now. */
  private ask<R>(uri: string, method: string, params: object, token?: CancellationToken): Promise<R> {
    if (this.closed) return Promise.reject(new Error('closed'))
    this.flush(uri)
    // An undefined last argument would be sent as a second positional parameter.
    return token
      ? this.connection.sendRequest<R>(method, params, token)
      : this.connection.sendRequest<R>(method, params)
  }

  private notify(method: string, params: object) {
    if (this.closed) return
    void this.connection.sendNotification(method, params).catch(() => {})
  }

  // ---------------------------------------------------------------- the end

  /** Asks the server to shut down, then closes the transport (which ends it either way). */
  async stop(): Promise<void> {
    if (this.closed) return
    try {
      await Promise.race([
        this.connection.sendRequest('shutdown'),
        new Promise((resolve) => setTimeout(resolve, SHUTDOWN_WAIT_MS)),
      ])
      await this.connection.sendNotification('exit')
    } catch {
      // Gone already: closing below is all that is left.
    }
    this.dispose()
  }

  /** Ends the conversation by closing the transport. */
  dispose(): void {
    if (this.closed) return
    this.transport.close()
    this.ended()
  }

  private ended() {
    if (this.closed) return
    this.closed = true
    for (const doc of this.documents.values()) if (doc.timer) clearTimeout(doc.timer)
    this.documents.clear()
    this.connection.dispose()
    for (const listener of [...this.closeListeners]) listener()
    this.closeListeners.clear()
    this.diagnosticListeners.clear()
  }
}
