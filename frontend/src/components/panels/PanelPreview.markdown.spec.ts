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
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'

vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))

const SOURCE = '# 配置\n\n失败以后重试 3 次。\n'

function props(path = 'output/说明.md', content = SOURCE) {
  return {
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

it('选中一段原文，定位条上写的是它在哪一节、原文是什么', async () => {
  const ui = mount()
  const md = ui.getByTestId('markdown')
  const node = md.querySelector('p')!.firstChild!
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
  const range = document.createRange()
  range.setStart(md.querySelector('p')!.firstChild!, 4)
  range.setEnd(md.querySelector('p')!.firstChild!, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeTruthy()

  await ui.rerender(props('output/另一份.md', '# 配置\n\n另一句话。\n'))

  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
})
