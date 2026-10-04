// @vitest-environment jsdom
// 修改记录：恢复的是看着的那一版，记在最新一版之上；最新那一版和不能改的文档没有恢复；
// 一个人连着存的几版算一条。
import type { DocVersion } from '../../../lib/docHistory'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import DocHistory from './DocHistory.vue'

import { setLocale, t } from '@/i18n'

beforeAll(() => {
  // 对话框打开时 Vuetify 要读它们来摆位置。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const at = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString()
const version = (n: number, actor: string, minutesAgo: number, content = `第 ${n} 版`): DocVersion => ({
  version: n,
  content,
  actor,
  requested_by: null,
  created_at: at(minutesAgo),
})

function mount(versions: DocVersion[], { editable = true } = {}) {
  const restore = vi.fn(async () => ({}))
  const view = render(DocHistory, {
    props: {
      open: true,
      load: async () => ({ versions, cursor: null }),
      restore,
      editable,
      nameOf: (handle: string) => handle,
      mentionNames: {},
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { ...view, restore }
}

describe('修改记录', () => {
  it('恢复的是选中的那一版，记在最新一版之上', async () => {
    const { restore } = mount([version(3, 'bob', 1), version(2, 'alice', 60), version(1, 'bob', 120)])
    await fireEvent.click(await screen.findByText('alice'))
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.restore') }))
    await waitFor(() => expect(restore).toHaveBeenCalledWith(2, 3))
  })

  it('最新那一版没有恢复', async () => {
    mount([version(3, 'bob', 1), version(2, 'alice', 60)])
    await screen.findAllByText('bob')
    expect(screen.queryByRole('button', { name: t('work.room.doc.restore') })).toBeNull()
  })

  it('不能改这篇文档时没有恢复', async () => {
    mount([version(3, 'bob', 1), version(2, 'alice', 60)], { editable: false })
    await fireEvent.click(await screen.findByText('alice'))
    expect(screen.queryByRole('button', { name: t('work.room.doc.restore') })).toBeNull()
  })

  it('同一个人连着存的几版算一条，别人插进来就分开', async () => {
    mount([
      version(5, 'bob', 1),
      version(4, 'bob', 2),
      version(3, 'bob', 3),
      version(2, 'alice', 4),
      version(1, 'bob', 5),
    ])
    await screen.findAllByText('alice')
    expect(screen.getAllByText('bob')).toHaveLength(2)
    expect(screen.getAllByText('alice')).toHaveLength(1)
  })

  it('右边默认是和上一条比的改动，能切到全文', async () => {
    const { container } = mount([version(2, 'bob', 1, '标题\n新加的一段\n结尾'), version(1, 'alice', 60, '标题\n结尾')])
    await screen.findAllByText('bob')
    await waitFor(() =>
      expect(container.ownerDocument.querySelector('.vdiff__row--add')?.textContent).toContain('新加的一段')
    )
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.historyFull') }))
    await waitFor(() => expect(container.ownerDocument.querySelector('.vdiff')).toBeNull())
  })

  it('第一版没有上一版：整篇算新加的', async () => {
    const { container } = mount([version(1, 'alice', 60, '只有一段')])
    await screen.findAllByText('alice')
    await waitFor(() =>
      expect(container.ownerDocument.querySelector('.vdiff__row--add')?.textContent).toContain('只有一段')
    )
  })

  it('最新一页全是同一个人连着存的：自动再读一页找上一条', async () => {
    const page1 = [version(30, 'bob', 1, '新'), version(29, 'bob', 2, '中')]
    const page2 = [version(28, 'alice', 60, '旧')]
    const load = vi.fn(async (before?: number) =>
      before === undefined ? { versions: page1, cursor: 29 } : { versions: page2, cursor: null }
    )
    const { container } = render(DocHistory, {
      props: { open: true, load, editable: true, nameOf: (h: string) => h, mentionNames: {} },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await waitFor(() => expect(load).toHaveBeenCalledWith(29))
    await waitFor(() => expect(container.ownerDocument.querySelector('.vdiff')).not.toBeNull())
  })
})
