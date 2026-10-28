/**
 * 预览这一格把「名字和头像怎么读」接给评论栏的那一段（`PanelPreviewView` 里的
 * `commentNameOf` / `commentAvatarOf`）。
 *
 * 为什么单有这一条：`preview/PreviewComments.spec.ts` 直接挂那一栏、自己把两个函数
 * 递给它，测的是「给了函数就叫它、就画它的结果」——那一栏本身没有毛病。出事的那一层
 * 是**这里**：接线处原来写的是「名字就是 handle」「头像给空串」，于是卡上显示 handle、
 * 画成色块，而类型检查、CI 全都不响。那两条单测永远不经过这里，所以把这一段换回占位
 * 值它们照样绿（实测过）。
 *
 * 这条用例挂的是这一格本身、从一个「有名字有头像」的包出发，所以接线退回占位值时，
 * 卡上会变成 `li`、图会消失，这里当场红。
 */
import type { PreviewCommentsBundle } from '../../composables/panelPreview/comments'
import type { DocThreadState } from '../../lib/docThreadTypes'

import { computed, reactive, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

// 文档那一档自己有条带（修订、历史、编辑器），这条用例只关心评论栏接得对不对。
vi.mock('./preview/PreviewDocStage.vue', () => ({
  default: {
    name: 'PreviewDocStage',
    props: ['documentName'],
    setup: () => ({ t: (s: string) => s }),
    template: '<div data-testid="doc-stage">{{ documentName }}</div>',
  },
}))
vi.mock('./preview/PreviewHeadBar.vue', () => ({ default: { name: 'PreviewHeadBar', template: '<div />' } }))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const THREADS: DocThreadState = {
  threads: [
    {
      comment: {
        id: 'c1',
        author: 'li',
        content: '这句话的用时要不要一起写进去？',
        anchor_quote: null,
        anchor: null,
        created_at: '2026-10-24T00:00:00Z',
      },
      revision: 1,
      state: 'open',
      replies: [],
    },
  ],
  activity: {},
  errors: {},
  busy: false,
  unknown: null,
}

/**
 * 评论那一包：名册上有这个人（名字、头像都有），所以卡上该写名字、该画那张图。
 *
 * 两个函数就是产品里 `usePanelPreviewComments` 从 `useDocPeople` 拿来的那两个
 * （`people.names.value[handle] ?? handle` 与 `people.avatars.value[handle] ?? ''`）。
 */
function comments(): PreviewCommentsBundle {
  const open = ref(true)
  return {
    target: computed(() => ({ topicId: 'room', path: 'docs/week-1.md', version: 'v1' })),
    open,
    toggle: () => {
      open.value = !open.value
    },
    author: 'me',
    threads: { state: reactive(THREADS), actions: {} as never, refresh: async () => {} },
    post: async () => {},
    nameOf: (handle: string) => ({ li: '李甘', wang: '王长鑫' })[handle] ?? handle,
    avatarOf: (handle: string) => (handle === 'li' ? '/avatars/4242' : ''),
  } as unknown as PreviewCommentsBundle
}

function props() {
  return {
    ...previewBundles(),
    topicId: 'room',
    projectId: 'project',
    frameName: 'frame',
    loading: false,
    refreshing: false,
    previewFile: {
      path: 'docs/week-1.md',
      content: '# 第一周\n',
      version: 'v1',
      bytes: 9,
      binary: false,
      too_large: false,
    },
    previewMime: 'text/markdown',
    previewNamed: true,
    previewUrl: null,
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: 'docs/week-1.md',
    previewError: null,
    previewReadError: null,
    documentSuffix: 'md',
    documentType: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' as const },
    documentName: 'week-1.md',
    isImageArtifact: false,
    downloadError: '',
    docBytes: null,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    docIdentity: { topicId: 'room', path: 'docs/week-1.md', taskId: null, source: 'live' as const, version: 'v1' },
    comments: comments(),
  }
}

function mount() {
  return render(PanelPreviewView, {
    props: props(),
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { VBtn: { template: '<button><slot /></button>' }, VIcon: true, VSpacer: true, VAlert: true },
    },
  })
}

it('评论卡上的名字和头像走的是接线处那一段，不是 handle 和空头像', async () => {
  const ui = mount()
  const column = await ui.findByLabelText('评论')
  // 名字：名册上他是「李甘」。接线退回 `(handle) => handle` 时这里会读到 `li`。
  expect(column.textContent).toContain('李甘')
  expect(column.textContent).not.toContain('li')
  // 头像：接线退回 `() => ''` 时就没有这张图，只剩彩色首字母。
  expect(column.querySelector('img')?.getAttribute('src')).toBe('/avatars/4242')
})
