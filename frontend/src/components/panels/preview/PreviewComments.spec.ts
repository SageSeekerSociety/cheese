/**
 * 文件评论栏上，两件曾经静默坏掉的事。
 *
 * 这一栏是浮层，盖住右边：开它的那颗按钮就在它底下。所以关闭入口必须长在栏自己
 * 身上——曾经没有，打开就关不掉。
 *
 * 每一张卡上写作者名、画作者头像，名字和头像都从名册那一包来（和文档评论栏同
 * 一处）。`nameOf`/`avatarOf` 曾经是选填的：不传不报错，卡上就显示 handle、画成
 * 色块——预览这一格当时正是这样，类型检查、测试、CI 全都不响。
 *
 * 这两条只测这一栏自己：把函数递给它，它就叫、就画。**出事的那一层不在这一栏**，
 * 在接线处（`PanelPreviewView` 把名册接进来的那两句）——把那里换回占位值，这两条
 * 照样绿。接线那一段由 `../PanelPreview.comments.spec.ts` 盯着（挂的是这一格本身）。
 */
import type { DocThread, DocThreadActions, DocThreadState } from '../../../lib/docThreadTypes'

import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PreviewComments from './PreviewComments.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const THREAD = {
  comment: {
    id: 'c1',
    author: 'wangchangxin',
    content: '这一处再核对一下。',
    anchor_quote: null,
    anchor: null,
    created_at: '2026-10-24T00:00:00Z',
  },
  revision: 1,
  state: 'open',
  replies: [],
} as unknown as DocThread

function mount(over: { nameOf?: (h: string) => string; avatarOf?: (h: string) => string } = {}) {
  return render(PreviewComments, {
    props: {
      topicId: 'room',
      path: 'deck.pdf',
      version: 'v1',
      author: 'me',
      threadState: {
        threads: [THREAD],
        activity: {},
        errors: {},
        busy: false,
        unknown: null,
      } as unknown as DocThreadState,
      threadActions: {} as unknown as DocThreadActions,
      pick: null,
      writable: true,
      post: vi.fn(),
      nameOf: over.nameOf ?? ((handle: string) => handle),
      avatarOf: over.avatarOf ?? (() => ''),
    },
    // 这一栏画的是真卡片（含头像、图标），按文档评论栏那边的规矩给一个 Vuetify 实例。
    global: { plugins: [createVuetify()] },
  })
}

it('栏上有自己的关闭入口，按了往上发 close', async () => {
  const ui = mount()
  const close = ui.getByLabelText('关闭评论')
  await fireEvent.click(close)
  expect(ui.emitted().close).toHaveLength(1)
})

it('卡上写的是名册给的名字，不是 handle', async () => {
  const ui = mount({ nameOf: () => '符露夀' })
  expect(ui.getByText('符露夀')).toBeTruthy()
  expect(ui.queryByText('wangchangxin')).toBeNull()
})

it('卡上的头像用的是名册给的地址，不是全站默认脸', async () => {
  const ui = mount({ avatarOf: () => '/avatars/4242' })
  expect(ui.container.querySelector('img')?.getAttribute('src')).toBe('/avatars/4242')
})
