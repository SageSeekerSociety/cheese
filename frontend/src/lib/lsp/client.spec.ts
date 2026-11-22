// The editor's LSP client against a fake server speaking real framed LSP:
//
//   1. Bytes arrive in chunks that mean nothing: a message split across chunks, or several in
//      one, each reaches the editor once and intact — multi-byte UTF-8 split mid-character too.
//   2. The server sees a document's text when it opens, the person's edits after (the latest
//      text, and before any question about it), and is told when it closes.
import type { PublishDiagnosticsParams } from 'vscode-languageserver-protocol'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { FakeLanguageServer, frame } from '../../test/fakeLanguageServer'

import { CHANGE_AFTER_MS, LspClient } from './client'

const ROOT = '/home/user/work'
const URI = 'file:///home/user/work/src/main.c'

function diagnostics(message: string): object {
  return {
    jsonrpc: '2.0',
    method: 'textDocument/publishDiagnostics',
    params: {
      uri: URI,
      diagnostics: [{ range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }, message }],
    },
  }
}

async function started() {
  const server = new FakeLanguageServer()
  const client = await LspClient.start(server.transport, ROOT)
  const heard: string[] = []
  client.onDiagnostics((p: PublishDiagnosticsParams) => heard.push(String(p.diagnostics[0]?.message ?? '')))
  return { server, client, heard }
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('the LSP client', () => {
  afterEach(() => vi.useRealTimers())

  it('opens with the checkout as the workspace root', async () => {
    const { server } = await started()
    const init = server.sent('initialize')[0].params as { rootUri: string }
    expect(init.rootUri).toBe('file:///home/user/work')
    expect(server.sent('initialized')).toHaveLength(1)
  })

  it('asks a question with the same parameters whether or not it can be cancelled', async () => {
    const { server, client } = await started()
    client.open(URI, 'c', 'int main(void) { return 0; }\n')
    const position = { line: 0, character: 4 }
    void client.definition(URI, position).catch(() => {})
    void client.hover(URI, position).catch(() => {})
    await settle()
    const asked = { textDocument: { uri: URI }, position }
    expect(server.sent('textDocument/definition')[0].params).toEqual(asked)
    expect(server.sent('textDocument/hover')[0].params).toEqual(asked)
  })

  it('delivers each message once and intact, however the bytes are chunked', async () => {
    const { server, heard } = await started()
    // Two messages in one chunk.
    const a = frame(diagnostics('first'))
    const b = frame(diagnostics('second'))
    const both = new Uint8Array(a.length + b.length)
    both.set(a)
    both.set(b, a.length)
    server.write(both)
    // One message cut into pieces, one cut through the middle of a three-byte character
    // and one through its header.
    const wide = frame(diagnostics('未声明的标识符 “x”'))
    const cut = wide.length - 12
    server.write(wide.subarray(0, 5))
    server.write(wide.subarray(5, cut - 1))
    server.write(wide.subarray(cut - 1, cut))
    server.write(wide.subarray(cut))
    await vi.waitFor(() => expect(heard).toHaveLength(3))
    expect(heard).toEqual(['first', 'second', '未声明的标识符 “x”'])
  })

  it('gives the server the text on open, the latest edit after, and says when it closes', async () => {
    const { server, client } = await started()
    client.open(URI, 'c', 'int main() {}\n')
    const opened = (await server.next('textDocument/didOpen', 0)).params as {
      textDocument: { uri: string; languageId: string; text: string }
    }
    expect(opened.textDocument).toMatchObject({ uri: URI, languageId: 'c', text: 'int main() {}\n' })

    client.change(URI, 'int main() { r }\n')
    client.change(URI, 'int main() { return 0; }\n')
    const changed = (await server.next('textDocument/didChange')).params as {
      contentChanges: { text: string }[]
    }
    expect(changed.contentChanges).toEqual([{ text: 'int main() { return 0; }\n' }])
    await new Promise((resolve) => setTimeout(resolve, CHANGE_AFTER_MS * 2))
    // Typing that settled went out once.
    expect(server.sent('textDocument/didChange')).toHaveLength(1)

    client.close(URI)
    const closed = (await server.next('textDocument/didClose')).params as { textDocument: { uri: string } }
    expect(closed.textDocument.uri).toBe(URI)
  })

  it('asks about the text the person sees, even while typing has not settled', async () => {
    const { server, client } = await started()
    server.handlers['textDocument/hover'] = () => ({ contents: 'ok' })
    client.open(URI, 'c', 'int x;\n')
    client.change(URI, 'int y;\n')
    await client.hover(URI, { line: 0, character: 4 })
    const order = server.received.map((m) => m.method)
    expect(order.indexOf('textDocument/didChange')).toBeGreaterThan(-1)
    expect(order.indexOf('textDocument/didChange')).toBeLessThan(order.indexOf('textDocument/hover'))
    const changed = server.sent('textDocument/didChange')[0].params as { contentChanges: { text: string }[] }
    expect(changed.contentChanges[0].text).toBe('int y;\n')
  })

  it('ends when the server goes away', async () => {
    const { server, client } = await started()
    let ended = false
    client.onClose(() => (ended = true))
    server.hangUp()
    await settle()
    expect(ended).toBe(true)
    expect(client.isClosed).toBe(true)
  })
})
