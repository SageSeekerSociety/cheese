/**
 * 归档的频道、没有「评论」权限的人：预览里那一份文件的评论照读，写的入口不摆。
 *
 * 和文档那一栏只读时一样（`PanelDocView` 把 `!readOnly` 递给评论栏的 `writable`）。这一格
 * 以前把 `writable` 写死成 true，于是归档频道里照样有输入框，写完点发送才被服务端 403。
 * 这里挂真实的评论包和整格视图，评论栏打开，看输入框在不在、已有的那条在不在。
 */
import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { usePanelPreviewComments } from '@/composables/panelPreview/comments'
import { createPreviewState } from '@/composables/panelPreview/previewState'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

const api = vi.hoisted(() => ({ listFileThreads: vi.fn(), postFileThread: vi.fn() }))
vi.mock('@/api/fileThreads', () => api)
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

const SOURCE = '# 配置\n\n失败以后重试 3 次。\n'
const FILE = {
  path: 'output/说明.md',
  content: SOURCE,
  version: 'v1',
  bytes: SOURCE.length,
  binary: false,
  too_large: false,
}

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  api.listFileThreads.mockResolvedValue({
    data: [
      {
        comment: { id: 'c1', author: 'li', content: '已有的那一条', anchor_quote: null, anchor: null, created_at: '' },
        revision: 1,
        state: 'open',
        replies: [],
      },
    ],
  })
})
afterEach(cleanup)

function mount(canComment: boolean | undefined) {
  const state = createPreviewState()
  state.previewFile.value = { ...FILE } as never
  const comments = usePanelPreviewComments({
    props: { topicId: 'room', projectId: 'project', members: [], canComment } as never,
    state,
    people: { names: ref({}), avatars: ref({}) } as never,
    docPage: () => false,
  })
  comments.toggle()
  const ui = render(PanelPreviewView, {
    props: {
      ...previewBundles(),
      topicId: 'room',
      projectId: 'project',
      frameName: 'frame',
      loading: false,
      refreshing: false,
      previewFile: { ...FILE },
      previewMime: 'text/markdown',
      previewNamed: true,
      previewUrl: null,
      previewAppNote: '',
      previewTunnelUp: false,
      previewNamedPath: FILE.path,
      previewError: null,
      previewReadError: null,
      documentSuffix: 'md',
      documentType: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' as const },
      documentName: '说明.md',
      isImageArtifact: false,
      docBytes: null,
      docLoading: false,
      docError: '',
      docRendererMissing: false,
      docIdentity: { topicId: 'room', path: FILE.path, taskId: null, source: 'live' as const, version: 'v1' },
      comments,
    },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { VBtn: { template: '<button><slot /></button>' }, VIcon: true, VSpacer: true, VAlert: true },
    },
  })
  return ui
}

it('不能写评论：评论栏里已有的那条照读，没有写新评论的输入框', async () => {
  const ui = mount(false)
  await waitFor(() => expect(ui.getByText('已有的那一条')).toBeTruthy(), { timeout: 5000 })
  expect(ui.container.querySelector('.file-comments__draft')).toBeNull()
})

it('能写评论：评论栏里有写新评论的输入框', async () => {
  const ui = mount(undefined)
  await waitFor(() => expect(ui.getByText('已有的那一条')).toBeTruthy(), { timeout: 5000 })
  expect(ui.container.querySelector('.file-comments__draft textarea')).toBeTruthy()
})
