/**
 * 「打开一份有人评过的 markdown，正文上那几处就画着标记」——从取数一路到画出来。
 *
 * 为什么单有这一条：`PanelPreview.markdown.spec.ts` 里那几条给的是**已经摆好线程的**评论包，
 * 于是「线程列表什么时候读」这一段根本不在它们经过的路上。真实产品里那一包是
 * `usePanelPreviewComments` 建的，它原先只在评论栏打开时才读——栏关着就没有线程，也就
 * 一个标记都不画。这一条用真实那一包挂整格，栏一直关着，所以那条门槛回来了它就红。
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
const ANCHOR = {
  kind: 'text-range',
  path: 'output/说明.md',
  source: 'live',
  version: 'v1',
  task_id: null,
  text: '重试 3 次',
  heading: '配置',
  prefix: '失败以后',
  suffix: '。',
}

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  api.listFileThreads.mockResolvedValue({
    data: [
      {
        comment: { id: 'c1', author: 'li', content: '第 1 条', anchor_quote: null, anchor: ANCHOR, created_at: '' },
        revision: 1,
        state: 'open',
        replies: [],
      },
    ],
  })
})
afterEach(cleanup)

it('打开一份有评论的 markdown，评论栏没开，正文上那几处也画着标记', async () => {
  const state = createPreviewState()
  state.previewFile.value = {
    path: 'output/说明.md',
    content: SOURCE,
    version: 'v1',
    bytes: SOURCE.length,
    binary: false,
    too_large: false,
  } as never
  const comments = usePanelPreviewComments({
    props: { topicId: 'room', projectId: 'project', members: [] } as never,
    state,
    people: { names: ref({}), avatars: ref({}) } as never,
    docPage: () => false,
  })
  expect(comments.open.value).toBe(false)

  const ui = render(PanelPreviewView, {
    props: {
      ...previewBundles(),
      topicId: 'room',
      projectId: 'project',
      frameName: 'frame',
      loading: false,
      refreshing: false,
      previewFile: {
        path: 'output/说明.md',
        content: SOURCE,
        version: 'v1',
        bytes: SOURCE.length,
        binary: false,
        too_large: false,
      },
      previewMime: 'text/markdown',
      previewNamed: true,
      previewUrl: null,
      previewAppNote: '',
      previewTunnelUp: false,
      previewNamedPath: 'output/说明.md',
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
      docIdentity: { topicId: 'room', path: 'output/说明.md', taskId: null, source: 'live' as const, version: 'v1' },
      comments,
    },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { VBtn: { template: '<button><slot /></button>' }, VIcon: true, VSpacer: true, VAlert: true },
    },
  })

  const md = ui.getByTestId('markdown')
  // 正文的读法是懒加载的：整目录并发跑时那一次 import 和评论串那一次取数都可能超过默认的 1 秒。
  const drawn = await waitFor(
    () => {
      const found = md.querySelector<HTMLElement>('mark[data-file-comment="c1"]')
      expect(found).toBeTruthy()
      return found!
    },
    { timeout: 5000 }
  )
  expect(drawn.textContent).toBe('重试 3 次')
  expect(comments.open.value).toBe(false)
})
