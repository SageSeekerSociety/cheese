/** 概览里的章节：按对话里的顺序编号，点一条回到那一节；说得上话的人能取消。 */
import type { Component } from 'vue'
import type { ChapterRow } from '@/types/chapters'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 菜单的弹层在测试环境里画不出来；这里只关心菜单里给了哪几项、点了做什么。
vi.mock('@/components/common/AdaptiveMenu.vue', () => ({
  default: {
    name: 'AdaptiveMenu',
    props: ['actions'],
    template:
      '<div><button v-for="a in actions" :key="a.key" type="button" @click="a.onSelect()">{{ a.label }}</button></div>',
  },
}))

import ChapterList from './ChapterList.vue'

import i18n, { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

function chapter(id: string, content: string): ChapterRow {
  return {
    pinned_by: 'alice',
    pinned_at: '2026-10-21T00:00:00Z',
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

// 钉的先后和对话里的先后相反：目录要照对话里的次序排。
const CHAPTERS = [chapter('b2', '再看发布'), chapter('b1', '先看登录')]

function mount(canChapter: boolean) {
  return render(ChapterList as unknown as Component, {
    props: { chapters: CHAPTERS, canChapter, memberNames: {}, refs: { mentionNames: {}, topicTitles: {} } },
    global: { plugins: [vuetify, i18n] },
  })
}

describe('章节目录', () => {
  it('按对话里的顺序编号，点一条回到那一节', async () => {
    const ui = mount(false)
    const rows = ui.getAllByRole('button').filter((el) => el.classList.contains('chapter__body'))
    expect(rows.map((el) => el.textContent)).toEqual([expect.stringContaining('1'), expect.stringContaining('2')])
    expect(rows[0]!.textContent).toContain('先看登录')
    expect(rows[1]!.textContent).toContain('再看发布')

    await fireEvent.click(rows[1]!)
    expect(ui.emitted().jump).toEqual([['b2']])
  })

  it('说得上话的人能取消章节，别人只能跳', async () => {
    const owner = mount(true)
    await fireEvent.click(owner.getAllByRole('button', { name: '取消章节' })[0]!)
    expect(owner.emitted().unpin).toEqual([['b1']])
    owner.unmount()

    const reader = mount(false)
    expect(reader.queryAllByRole('button', { name: '取消章节' })).toHaveLength(0)
    expect(reader.getAllByRole('button', { name: '跳到这一条' })).toHaveLength(2)
  })
})
