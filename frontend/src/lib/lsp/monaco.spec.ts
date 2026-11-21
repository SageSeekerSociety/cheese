// A task file in Monaco, answered by a fake language server speaking real framed LSP:
//
//   1. What the server publishes about a file becomes that file's squiggles, at the ranges it
//      named, and goes when the server publishes none (or goes away).
//   2. Completion, hover, definition and references come back in Monaco's terms: 1-based
//      positions, the checkout's files as the task's models, other files as themselves.
//   3. A model that is not this task's file gets nothing from the server.
//   4. Go to definition into another file of the checkout opens that file, at that line.
import type * as Monaco from 'monaco-editor'
import type { LanguageLink } from './link'

import { beforeAll, describe, expect, it } from 'vitest'

import { FakeLanguageServer } from '../../test/fakeLanguageServer'

import { LspClient } from './client'
import { closeDocument, modelUri, openDocument, openerFor, providers } from './monaco'

type MonacoApi = typeof Monaco
let monaco: MonacoApi

beforeAll(async () => {
  // Monaco's core without its language contributions: the same editor API, loaded in seconds.
  monaco = (await import('monaco-editor/esm/vs/editor/editor.api.js')) as unknown as MonacoApi
  // …plus the one contribution these tests drive: go to definition.
  await import('monaco-editor/esm/vs/editor/contrib/gotoSymbol/browser/goToCommands.js')
  // The full build registers C with its grammar; the core alone knows no languages.
  monaco.languages.register({ id: 'c' })
}, 60_000)

const ROOT = '/srv/checkout'
const token = { isCancellationRequested: false, onCancellationRequested: () => ({ dispose() {} }) }
let scopes = 0

async function setUp(text = 'int main(void) {\n  return x;\n}\n') {
  const server = new FakeLanguageServer()
  const client = await LspClient.start(server.transport, ROOT)
  const opened: { path: string; line: number }[] = []
  const listeners = new Set<(c: LspClient | null) => void>()
  const link: LanguageLink = {
    scope: `task-${++scopes}`,
    serverFor: (languageId) =>
      languageId === 'c'
        ? {
            use(listener) {
              listeners.add(listener)
              listener(client)
              return () => listeners.delete(listener)
            },
          }
        : null,
    open: (path, line) => opened.push({ path, line }),
  }
  const model = openDocument(monaco, link, 'src/main.c', 'c', text)
  await server.next('textDocument/didOpen', 0)
  const gone = () => listeners.forEach((l) => l(null))
  return { server, client, link, model, opened, gone }
}

const markers = (model: Monaco.editor.ITextModel) => monaco.editor.getModelMarkers({ resource: model.uri })
const range = (sl: number, sc: number, el: number, ec: number) => ({
  start: { line: sl, character: sc },
  end: { line: el, character: ec },
})

describe('a task file in the editor', () => {
  it("shows the server's diagnostics for that file at their ranges, and clears them", async () => {
    const { server, model } = await setUp()
    server.notify('textDocument/publishDiagnostics', {
      uri: 'file:///srv/checkout/src/main.c',
      diagnostics: [
        { range: range(1, 9, 1, 10), severity: 1, message: "use of undeclared identifier 'x'", source: 'clang' },
        { range: range(0, 4, 0, 8), severity: 2, message: 'unused' },
      ],
    })
    // Another file's diagnostics are not this file's.
    server.notify('textDocument/publishDiagnostics', {
      uri: 'file:///srv/checkout/src/other.c',
      diagnostics: [{ range: range(0, 0, 0, 1), message: 'elsewhere' }],
    })
    await expect.poll(() => markers(model).length).toBe(2)
    const error = markers(model).find((m) => m.severity === monaco.MarkerSeverity.Error)!
    expect(error).toMatchObject({
      message: "use of undeclared identifier 'x'",
      startLineNumber: 2,
      startColumn: 10,
      endLineNumber: 2,
      endColumn: 11,
    })
    expect(markers(model).some((m) => m.severity === monaco.MarkerSeverity.Warning)).toBe(true)

    server.notify('textDocument/publishDiagnostics', { uri: 'file:///srv/checkout/src/main.c', diagnostics: [] })
    await expect.poll(() => markers(model).length).toBe(0)
    closeDocument(model)
  })

  it('drops the squiggles when the server goes away', async () => {
    const { server, model, gone } = await setUp()
    server.notify('textDocument/publishDiagnostics', {
      uri: 'file:///srv/checkout/src/main.c',
      diagnostics: [{ range: range(0, 0, 0, 3), message: 'bad' }],
    })
    await expect.poll(() => markers(model).length).toBe(1)
    gone()
    expect(markers(model)).toHaveLength(0)
    closeDocument(model)
  })

  it('sends what the person types to the server, and closes the file there when the editor lets go', async () => {
    const { server, model } = await setUp('int a;\n')
    model.setValue('int ab;\n')
    const changed = (await server.next('textDocument/didChange')).params as { contentChanges: { text: string }[] }
    expect(changed.contentChanges[0].text).toBe('int ab;\n')
    closeDocument(model)
    const closed = (await server.next('textDocument/didClose')).params as { textDocument: { uri: string } }
    expect(closed.textDocument.uri).toBe('file:///srv/checkout/src/main.c')
    expect(model.isDisposed()).toBe(true)
  })

  it('offers completions at the server’s range, snippets as snippets', async () => {
    const { server, model } = await setUp('int main(void) {\n  pri\n}\n')
    server.handlers['textDocument/completion'] = () => ({
      isIncomplete: false,
      items: [
        {
          label: 'printf',
          kind: 3,
          insertTextFormat: 2,
          textEdit: { range: range(1, 2, 1, 5), newText: 'printf(${1:fmt})' },
          detail: 'int',
        },
      ],
    })
    const list = await providers(monaco).completion.provideCompletionItems(
      model,
      new monaco.Position(2, 6),
      { triggerKind: monaco.languages.CompletionTriggerKind.Invoke },
      token
    )
    const [item] = list!.suggestions
    expect(item).toMatchObject({
      label: 'printf',
      insertText: 'printf(${1:fmt})',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      kind: monaco.languages.CompletionItemKind.Function,
      range: { startLineNumber: 2, startColumn: 3, endLineNumber: 2, endColumn: 6 },
    })
    const sent = server.sent('textDocument/completion')[0].params as { position: object }
    expect(sent.position).toEqual({ line: 1, character: 5 })

    server.handlers['completionItem/resolve'] = (item) => ({ ...(item as object), documentation: 'Prints.' })
    const resolved = await providers(monaco).completion.resolveCompletionItem!(item, token)
    expect(resolved?.documentation).toBe('Prints.')
    closeDocument(model)
  })

  it('shows the hover the server gives, as Markdown', async () => {
    const { server, model } = await setUp()
    server.handlers['textDocument/hover'] = () => ({
      contents: { kind: 'markdown', value: '### function `main`' },
      range: range(0, 4, 0, 8),
    })
    const hover = await providers(monaco).hover.provideHover(model, new monaco.Position(1, 6), token)
    expect(hover?.contents).toEqual([{ value: '### function `main`' }])
    expect(hover?.range).toEqual({ startLineNumber: 1, startColumn: 5, endLineNumber: 1, endColumn: 9 })
    closeDocument(model)
  })

  it("names a definition in the checkout by the task's file, and one outside by its own path", async () => {
    const { server, model, link } = await setUp()
    server.handlers['textDocument/definition'] = () => [
      { uri: 'file:///srv/checkout/include/x%20y.h', range: range(4, 0, 4, 5) },
      {
        targetUri: 'file:///usr/include/stdio.h',
        targetRange: range(9, 0, 12, 1),
        targetSelectionRange: range(10, 4, 10, 10),
      },
    ]
    const found = (await providers(monaco).definition.provideDefinition(
      model,
      new monaco.Position(2, 10),
      token
    )) as Monaco.languages.LocationLink[]
    expect(found[0].uri.toString()).toBe(modelUri(monaco, link.scope, 'include/x y.h').toString())
    expect(found[0].range).toEqual({ startLineNumber: 5, startColumn: 1, endLineNumber: 5, endColumn: 6 })
    expect(found[1].uri.toString()).toBe('file:///usr/include/stdio.h')
    expect(found[1].targetSelectionRange).toEqual({
      startLineNumber: 11,
      startColumn: 5,
      endLineNumber: 11,
      endColumn: 11,
    })
    closeDocument(model)
  })

  it('lists references as locations in the task’s files', async () => {
    const { server, model, link } = await setUp()
    server.handlers['textDocument/references'] = () => [
      { uri: 'file:///srv/checkout/src/main.c', range: range(1, 9, 1, 10) },
      { uri: 'file:///srv/checkout/src/util.c', range: range(3, 2, 3, 3) },
    ]
    const found = await providers(monaco).references.provideReferences(
      model,
      new monaco.Position(2, 10),
      { includeDeclaration: true },
      token
    )
    expect(found?.map((l) => [l.uri.toString(), l.range.startLineNumber, l.range.startColumn])).toEqual([
      [model.uri.toString(), 2, 10],
      [modelUri(monaco, link.scope, 'src/util.c').toString(), 4, 3],
    ])
    const sent = server.sent('textDocument/references')[0].params as { context: object }
    expect(sent.context).toEqual({ includeDeclaration: true })
    closeDocument(model)
  })

  it('gives nothing for a model that is not a file of the task', async () => {
    const { server, model } = await setUp()
    server.handlers['textDocument/hover'] = () => ({ contents: 'should not be asked' })
    const stranger = monaco.editor.createModel('int y;\n', 'c')
    const p = providers(monaco)
    expect(await p.hover.provideHover(stranger, new monaco.Position(1, 5), token)).toBeNull()
    expect(
      await p.completion.provideCompletionItems(
        stranger,
        new monaco.Position(1, 5),
        { triggerKind: monaco.languages.CompletionTriggerKind.Invoke },
        token
      )
    ).toBeUndefined()
    expect(server.sent('textDocument/hover')).toHaveLength(0)
    stranger.dispose()
    closeDocument(model)
  })

  it('opens another file of the checkout at its line on go to definition', async () => {
    const { server, model, link, opened } = await setUp()
    server.handlers['textDocument/definition'] = () => [
      { uri: 'file:///srv/checkout/src/util.c', range: range(6, 4, 6, 9) },
    ]
    // happy-dom has no canvas; Monaco draws its rulers on one. A context that draws nothing.
    const proto = HTMLCanvasElement.prototype as unknown as { getContext?: unknown }
    const getContext = proto.getContext
    const blank = new Proxy({}, { get: (_, key) => (String(key).endsWith('PixelRatio') ? undefined : () => {}) })
    proto.getContext = () => blank
    const host = document.createElement('div')
    document.body.appendChild(host)
    const editor = monaco.editor.create(host, { model, minimap: { enabled: false }, overviewRulerLanes: 0 })
    const stop = openerFor(monaco, editor, link)
    editor.setPosition({ lineNumber: 2, column: 10 })
    editor.focus()
    editor.trigger('test', 'editor.action.revealDefinition', null)
    await expect.poll(() => opened).toEqual([{ path: 'src/util.c', line: 7 }])
    stop()
    editor.dispose()
    host.remove()
    proto.getContext = getContext
    closeDocument(model)
  })
})
