/** 左上角书签条：没有书签就不画；点一条跳回去；说得上话的人能就地改名、取消。 */
import type { Component } from 'vue'
import type { ChapterRow } from '@/types/chapters'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 菜单的弹层在测试环境里画不出来；这里只关心菜单里给了哪几项、点了做什么，并模拟打开菜单时的 modelValue。
vi.mock('@/components/common/AdaptiveMenu.vue', () => ({
  default: {
    name: 'AdaptiveMenu',
    props: ['actions', 'modelValue'],
    emits: ['update:modelValue'],
    template:
      '<div><button v-for="a in actions" :key="a.key" type="button" @click="$emit(\'update:modelValue\', true); a.onSelect()">{{ a.label }}</button></div>',
  },
}))

import BookmarkRail from './BookmarkRail.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

function bookmark(id: string, title: string | null, content = '这条消息的第一行'): ChapterRow {
  return {
    pinned_by: 'alice',
    pinned_at: '2026-10-21T00:00:00Z',
    title,
    block: {
      id,
      conversation_id: 'r1',
      kind: 'message',
      author: 'alice',
      author_type: 'participant',
      content,
      created_at: '2026-10-21T00:00:00Z',
    },
  } as unknown as ChapterRow
}

function mount(chapters: ChapterRow[], canChapter = true) {
  return render(BookmarkRail as unknown as Component, {
    props: { chapters, canChapter, refs: { mentionNames: {}, topicTitles: {} } },
    global: { plugins: [vuetify, i18n] },
  })
}

async function mountOpen(chapters: ChapterRow[], canChapter = true) {
  const ui = mount(chapters, canChapter)
  const rail = ui.container.querySelector('[data-testid="bookmark-rail"]')
  if (rail) await fireEvent.mouseEnter(rail)
  return ui
}

describe('对话左上角的书签条', () => {
  it('没有书签就整块不画', () => {
    const { container } = mount([])
    expect(container.querySelector('[data-testid="bookmark-rail"]')).toBeNull()
  })

  it('默认收起成一颗小角标，鼠标移上去或点一下展开，移开自动收起', async () => {
    const ui = mount([bookmark('b1', '登录')])
    expect(ui.queryByRole('button', { name: '展开书签' })).toBeTruthy()
    expect(ui.queryByRole('button', { name: '登录' })).toBeNull()

    const rail = ui.getByTestId('bookmark-rail')
    await fireEvent.mouseEnter(rail)
    expect(ui.queryByRole('button', { name: '收起书签' })).toBeTruthy()
    expect(ui.getByRole('button', { name: '登录' })).toBeTruthy()

    await fireEvent.mouseLeave(rail)
    expect(ui.queryByRole('button', { name: '展开书签' })).toBeTruthy()
    expect(ui.queryByRole('button', { name: '登录' })).toBeNull()
  })

  it('按对话里的顺序编号，名字优先用存下来的那一版', async () => {
    const ui = await mountOpen([bookmark('b2', '发布'), bookmark('b1', '登录')])
    const rows = ui.getAllByRole('button').filter((el) => el.classList.contains('bm-rail__jump'))

    expect(rows[0]!.textContent).toContain('1')
    expect(rows[0]!.textContent).toContain('登录')
    expect(rows[1]!.textContent).toContain('2')
    expect(rows[1]!.textContent).toContain('发布')

    await fireEvent.click(rows[1]!)
    expect(ui.emitted().jump).toEqual([['b2']])
    expect(ui.queryByRole('button', { name: '展开书签' })).toBeTruthy()
  })

  it('没有名字时退回消息的第一行', async () => {
    const ui = await mountOpen([bookmark('b1', null, '把三条路径再过一遍')])
    expect(ui.getByText('把三条路径再过一遍')).toBeTruthy()
  })

  it('说得上话的人能在 ⋯ 里改名与取消，改完移开照常收起', async () => {
    const ui = await mountOpen([bookmark('b1', '登录')])
    await fireEvent.click(ui.getByRole('button', { name: '重命名' }))

    const field = ui.getByRole('textbox', { name: '书签名字' })
    await fireEvent.update(field, '登录页')
    await fireEvent.keyDown(field, { key: 'Enter' })

    expect(ui.emitted().rename).toEqual([['b1', '登录页']])

    await fireEvent.click(ui.getByRole('button', { name: '取消书签' }))
    expect(ui.emitted().unpin).toEqual([['b1']])

    await fireEvent.mouseLeave(ui.getByTestId('bookmark-rail'))
    expect(ui.queryByRole('button', { name: '展开书签' })).toBeTruthy()
  })

  it('没资格改的人只有跳回这一条', async () => {
    const ui = await mountOpen([bookmark('b1', '登录')], false)
    expect(ui.queryByRole('button', { name: '重命名' })).toBeNull()
    expect(ui.queryByRole('button', { name: '取消书签' })).toBeNull()
    expect(ui.getByRole('button', { name: '跳到这一条' })).toBeTruthy()
  })
})
