// The file editor with and without a task's language servers:
//
//   1. Without a link it is the editor it always was: switching file shows the new text in the
//      same editor, typing is emitted, and no file of a task is made.
//   2. With one, the file open in it is the server's to see: its text on open, the typing after.
import type * as Monaco from 'monaco-editor'
import type { LanguageLink } from '../lib/lsp/link'

import { createVuetify } from 'vuetify'
import { render } from '@testing-library/vue'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'

import { LspClient } from '../lib/lsp/client'
import { FakeLanguageServer } from '../test/fakeLanguageServer'

import CodeEditor from './CodeEditor.vue'

// Monaco's core: the same editor API as the full build, loaded in seconds.
vi.mock('monaco-editor', async () => {
  const api = await import('monaco-editor/esm/vs/editor/editor.api.js')
  api.languages.register({ id: 'c' })
  return api
})
const worker = { default: class {} }
vi.mock('monaco-editor/esm/vs/editor/editor.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/css/css.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/html/html.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/json/json.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/typescript/ts.worker?worker', () => worker)

let monaco: typeof Monaco
// happy-dom has no canvas; Monaco draws its rulers on one. A context that draws nothing.
const proto = HTMLCanvasElement.prototype as unknown as { getContext?: unknown }
const getContext = proto.getContext
beforeAll(async () => {
  const blank = new Proxy({}, { get: (_, key) => (String(key).endsWith('PixelRatio') ? undefined : () => {}) })
  proto.getContext = () => blank
  monaco = (await import('monaco-editor')) as unknown as typeof Monaco
}, 60_000)
afterAll(() => {
  proto.getContext = getContext
})

const vuetify = createVuetify()

async function shown(): Promise<Monaco.editor.ICodeEditor> {
  await vi.waitFor(() => expect(monaco.editor.getEditors()).toHaveLength(1), { timeout: 20_000 })
  return monaco.editor.getEditors()[0]
}

function type(editor: Monaco.editor.ICodeEditor, text: string) {
  const end = editor.getModel()!.getFullModelRange().getEndPosition()
  editor.executeEdits('test', [{ range: monaco.Range.fromPositions(end), text }])
}

describe('the file editor', () => {
  it('without a link, switches file in the same editor and emits what is typed', async () => {
    const view = render(CodeEditor, {
      props: { modelValue: 'first\n', filename: 'a.txt' },
      global: { plugins: [vuetify] },
    })
    const editor = await shown()
    expect(editor.getValue()).toBe('first\n')

    await view.rerender({ modelValue: 'second\n', filename: 'b.txt' })
    expect(monaco.editor.getEditors()).toEqual([editor])
    expect(editor.getValue()).toBe('second\n')

    type(editor, 'more')
    expect(view.emitted('update:modelValue')?.at(-1)).toEqual(['second\nmore'])
    expect(monaco.editor.getModels().some((m) => m.uri.scheme === 'cheese-task')).toBe(false)
    view.unmount()
  }, 30_000)

  it("with a link, gives the task's server the file's text and then the typing", async () => {
    const server = new FakeLanguageServer()
    const client = await LspClient.start(server.transport, '/work')
    const link: LanguageLink = {
      scope: 'task-1',
      serverFor: (id) => (id === 'c' ? { use: (listener) => (listener(client), () => {}) } : null),
      open: () => {},
    }
    const view = render(CodeEditor, {
      props: { modelValue: 'int a;\n', filename: 'src/a.c', language: link },
      global: { plugins: [vuetify] },
    })
    const editor = await shown()
    const opened = (await server.next('textDocument/didOpen', 0)).params as {
      textDocument: { uri: string; text: string }
    }
    expect(opened.textDocument).toMatchObject({ uri: 'file:///work/src/a.c', text: 'int a;\n' })

    type(editor, 'int b;\n')
    const changed = (await server.next('textDocument/didChange')).params as { contentChanges: { text: string }[] }
    expect(changed.contentChanges[0].text).toBe('int a;\nint b;\n')

    view.unmount()
    await server.next('textDocument/didClose')
  }, 30_000)
})
