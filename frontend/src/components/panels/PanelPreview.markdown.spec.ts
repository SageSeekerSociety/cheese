/**
 * markdown 那一支的指认：位置来自渲染出来的正文（哪个标题之下、原文、两侧各 32 字），
 * 而屏幕上换了另一份文件时这条指认必须自己撤掉——它说的是文件 A 里的位置，发出去时
 * 名字写的却是文件 B。
 *
 * 三分支走的是同一套事件（`PanelPreview.document.spec.ts` 里另有端到端的一条），
 * 这里只盯这两条规则，所以直接挂 `PanelPreviewView`、把 props 递进去。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))

const SOURCE = '# 配置\n\n失败以后重试 3 次。\n'

function props(path = 'output/说明.md', content = SOURCE) {
  return {
    ...previewBundles(),
    topicId: 'room',
    projectId: 'project',
    frameName: 'frame',
    loading: false,
    refreshing: false,
    previewFile: {
      path,
      content,
      version: 'v1',
      bytes: content.length,
      binary: false,
      too_large: false,
    },
    previewMime: 'text/markdown',
    previewNamed: true,
    previewUrl: null,
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: path,
    previewError: null,
    previewReadError: null,
    documentSuffix: 'md',
    documentType: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' as const },
    documentName: '说明.md',
    isImageArtifact: false,
    downloadError: '',
    docBytes: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    // 这一份文件在屏幕上的身份：发出去的结构化引用要带上它（path/source/version）。
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

/** 正文第一段的字。正文的读法第一次用到才加载，画出来要等一下。 */
function firstLine(md: HTMLElement): Promise<ChildNode> {
  return waitFor(() => md.querySelector('p')!.firstChild!)
}

it('选中一段原文，定位条上写的是它在哪一节、原文是什么', async () => {
  const ui = mount()
  const md = ui.getByTestId('markdown')
  const node = await firstLine(md)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)

  expect(ui.getByText('标题「配置」')).toBeTruthy()
  expect(ui.getByText('重试 3 次')).toBeTruthy()
  // 选中只是把输入框叫出来，还没有一句话要说，所以什么都没有发出去。
  expect(ui.emitted().locate).toBeUndefined()
})

it('屏幕上换成另一份文件时，这条指认自己撤掉', async () => {
  const ui = mount()
  const md = ui.getByTestId('markdown')
  const node = await firstLine(md)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeTruthy()

  await ui.rerender(props('output/另一份.md', '# 配置\n\n另一句话。\n'))

  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
})

/** 在「配置」那一节里选中「重试 3 次」这一段，和上面两条一样。 */
async function selectPassage(ui: ReturnType<typeof mount>) {
  const md = ui.getByTestId('markdown')
  const node = await firstLine(md)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)
}

it('选中一段发出去时带的是带文件身份的 text-range 引用', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount({ submitQuestion: submit })
  await selectPassage(ui)
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '改这句')
  await fireEvent.click(ui.getByText('发送'))

  const request = submit.mock.calls[0]?.[0]
  expect(request).toMatchObject({ topicId: 'room', intent: 'ask-agent', content: '改这句' })
  expect(request.quotedContext).toMatchObject({
    kind: 'text-range',
    path: 'output/说明.md',
    source: 'live',
    version: 'v1',
    task_id: null,
    text: '重试 3 次',
    heading: '配置',
  })
  // 两侧各带一段前后文，受话人靠它分辨同一句话的哪一处出现。
  expect(request.quotedContext.prefix.endsWith('失败以后')).toBe(true)
  expect(request.quotedContext.suffix.startsWith('。')).toBe(true)
  // 走的是结构化那条路，不再额外拼一句话。
  expect(ui.emitted().locate).toBeUndefined()
})

it('拿不到文件身份时退回拼一句话那条老路', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount({ submitQuestion: submit, docIdentity: null })
  await selectPassage(ui)
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '改这句')
  await fireEvent.click(ui.getByText('发送'))

  expect(submit).not.toHaveBeenCalled()
  const [payload] = ui.emitted().locate as { message: string }[][]
  expect(payload[0].message).toContain('重试 3 次')
  expect(payload[0].message).toContain('改这句')
})
