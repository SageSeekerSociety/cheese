/** 反应条悬停列的是谁：房间按名册算好的显示名，不是 handle。芝士的署名
 *  `cheese-<hex>` 是管道，摆到屏幕上没人认得出；不认识它时写「芝士」，也不写它。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import RoomMessage from './RoomMessage.vue'

import i18n, { setLocale } from '@/i18n'

function mount(
  reactions: { emoji: string; count: number; authors: string[] }[],
  nameOfHandle?: (handle: string) => string
) {
  return render(RoomMessage, {
    props: {
      block: { id: 'b1', author: 'alice', content: 'hi', kind: 'message', created_at: '', reactions },
      parent: null,
      parentName: null,
      runStart: true,
      mine: false,
      topicId: 't1',
      authorName: 'Alice',
      avatar: null,
      isAgent: false,
      time: '10:00',
      refs: { mentionNames: {}, topicTitles: {} },
      nameOfHandle,
      viewer: 'alice',
    } as never,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

function naming(names: Record<string, string>): (handle: string) => string {
  return (handle) => names[handle] ?? handle
}

beforeEach(() => setLocale('zh-CN'))

describe('反应条的悬停提示', () => {
  it('芝士的回执写名册上的名字，不写座位 handle', () => {
    const { container } = mount(
      [{ emoji: '👀', count: 1, authors: ['cheese-f2f12a4efaf3'] }],
      naming({ 'cheese-f2f12a4efaf3': '芝士ni' })
    )
    expect(container.querySelector('.rx-chip')?.getAttribute('title')).toBe('芝士ni')
  })

  it('房间没给名字时也不把芝士的 handle 摆到屏幕上', () => {
    const { container } = mount([{ emoji: '👀', count: 1, authors: ['cheese-unknown'] }])
    expect(container.querySelector('.rx-chip')?.getAttribute('title')).toBe('芝士')
  })

  it('多人点了同一种表情：按名册逐个写名字，认不出的人照旧显示 handle', () => {
    const { container } = mount(
      [{ emoji: '👍', count: 2, authors: ['cheese-f2f12a4efaf3', 'gone-user'] }],
      naming({ 'cheese-f2f12a4efaf3': '芝士ni' })
    )
    expect(container.querySelector('.rx-chip')?.getAttribute('title')).toBe('芝士ni、gone-user')
  })
})
