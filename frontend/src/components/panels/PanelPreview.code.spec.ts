/**
 * 代码那一支：一份 .py 摆进预览，按行带行号画出来（不进 iframe），选中几行就把「第 N–M 行」
 * 和这几行的原文递进定位条，发出去的那句话里写的就是行号。
 *
 * 类型取自真的 `DOCUMENT_TYPES`：一份 .py 认不成代码（表里没有它）时，这里第一条就红。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { DOCUMENT_TYPES } from '@/lib/fileKind'
import { previewBundles } from '@/test/panelBundles'

vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

const SOURCE = 'import os\n\n\ndef main():\n    """入口"""\n    return os.getcwd()\n'

function props(path = 'scripts/tool.py', content: string | null = SOURCE) {
  const suffix = path.split('.').pop()!
  return {
    ...previewBundles(),
    topicId: 'room',
    projectId: 'project',
    frameName: 'frame',
    loading: false,
    refreshing: false,
    previewFile: { path, content, version: 'v1', bytes: content?.length ?? 0, binary: false, too_large: false },
    previewMime: 'text/plain',
    previewNamed: true,
    previewUrl: null,
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: path,
    previewError: null,
    previewReadError: null,
    documentSuffix: suffix,
    documentType: DOCUMENT_TYPES[suffix] ?? null,
    documentName: path.split('/').pop()!,
    isImageArtifact: false,
    downloadError: '',
    docBytes: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    docIdentity: { topicId: 'room', path, taskId: null, source: 'live' as const, version: 'v1' },
  }
}

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  window.getSelection()?.removeAllRanges()
})

function mount(overrides: Record<string, unknown> = {}) {
  return render(PanelPreviewView, {
    props: { ...props(), ...overrides },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { VBtn: { template: '<button><slot /></button>' }, VIcon: true, VSpacer: true, VAlert: true },
    },
  })
}

function code(ui: ReturnType<typeof mount>, line: number): Element {
  return ui.getByTestId('code-view').querySelector(`[data-line="${line}"] [data-code]`)!
}

it('一份 .py 按行画出来，带行号和高亮，文件条上写的是 Python', async () => {
  const ui = mount()
  const view = ui.getByTestId('code-view')
  expect(view.querySelectorAll('[data-line]')).toHaveLength(6)
  expect(view.querySelector('[data-line="4"] [data-gutter]')!.textContent).toBe('4')
  expect(view.querySelector('.hljs-keyword')!.textContent).toBe('import')
  expect(ui.getByText('Python')).toBeTruthy()
})

it('拖选落在哪几行，定位条上就写第几行到第几行；发出去的那句话里也是行号', async () => {
  const ui = mount()
  // 从第 4 行中间拖到第 6 行，再多拖到「下一行的行首」也不算第 7 行。
  const range = document.createRange()
  range.setStart(code(ui, 4).firstChild!, 0)
  range.setEnd(code(ui, 6).lastChild!, 1)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
  await fireEvent.mouseUp(code(ui, 6))

  expect(ui.getByText('第 4–6 行')).toBeTruthy()
  await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '这里换成 pathlib')
  await fireEvent.click(ui.getByText('发送'))

  await waitFor(() => expect(ui.emitted().locate).toBeTruthy())
  const message = (ui.emitted().locate as { message: string }[][])[0][0].message
  expect(message.startsWith('在 scripts/tool.py 的 第 4–6 行（「def main():')).toBe(true)
  expect(message.endsWith('return os.getcwd()」）：这里换成 pathlib')).toBe(true)
})

it('点行号选一行，按住 Shift 再点一行选中间那一片', async () => {
  const ui = mount()
  const gutter = (line: number) => ui.getByTestId('code-view').querySelector(`[data-line="${line}"] [data-gutter]`)!

  await fireEvent.click(gutter(1))
  expect(ui.getByText('第 1 行')).toBeTruthy()

  await fireEvent.click(gutter(4), { shiftKey: true })
  expect(ui.getByText('第 1–4 行')).toBeTruthy()
  expect(window.getSelection()!.toString()).toContain('def main():')
})

it('读不成文本的代码文件就地说一句，不落到表格那一支', async () => {
  const ui = mount({ previewFile: { ...props().previewFile, content: null } })
  expect(ui.queryByTestId('code-view')).toBeNull()
  expect(ui.queryByTestId('sheet')).toBeNull()
  expect(ui.getByText('这个文件不是文本')).toBeTruthy()
})
