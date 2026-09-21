/** 话题页顶栏上那个状态徽章跟着语言走。
 *
 * 徽章上的词来自 `lib/topicState.ts`，而它和看板列名共用同一张表（`workspace.status.*`）
 * ——所以这一份钉的是「同一个词在任何一屏都是同一个词」：同一条话题，给不同的
 * phase，徽章要念出那一段该念的词，而且中英各念各的。
 *
 * 扫的只有 `.pr-state` 这一个元素。顶栏上还住着别的字（用量、未连接、算力…），
 * 那些是这一屏自己欠着的，不属于这一刀。
 */
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicHeader from './TopicHeader.vue'

import { setLocale } from '@/i18n'

const Header = TopicHeader as unknown as Component

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  // Vuetify 的断点读 window.innerWidth。桌面宽度下这一行不 Teleport 到手机顶栏，
  // 徽章就在渲染出来的容器里，取得到。
  Object.defineProperty(window, 'innerWidth', { value: 1280, writable: true, configurable: true })
})

beforeEach(() => {
  vi.stubGlobal('visualViewport', new EventTarget())
})

function mount(props: Record<string, unknown> = {}) {
  return render(Header, {
    props: { topic, members: [], me: 'ligan', connected: true, focus: false, ...props },
    global: {
      plugins: [vuetify],
      // 名册和算力选择器各拉各的接口，这一份只看徽章。
      stubs: { TopicComputePicker: true, TopicMembers: true },
    },
  })
}

const badge = (container: Element) => container.querySelector('.pr-state') as HTMLElement

describe('讲中文', () => {
  it('话题处在哪一段就念哪一段的词', async () => {
    setLocale('zh-CN')
    const { container, rerender } = mount({ phase: 'working' })
    expect(badge(container).textContent?.trim()).toBe('施工中')

    await rerender({ topic, members: [], me: 'ligan', connected: true, focus: false, phase: 'reviewing' })
    expect(badge(container).textContent?.trim()).toBe('待验收')

    await rerender({ topic, members: [], me: 'ligan', connected: true, focus: false, phase: 'delivering' })
    expect(badge(container).textContent?.trim()).toBe('交付中')
  })

  it('没有 phase 的话题退回它的 status：归档读作「已采纳」', () => {
    setLocale('zh-CN')
    const { container } = mount({ topic: { ...topic, status: 'archived' } })
    expect(badge(container).textContent?.trim()).toBe('已采纳')
    expect(badge(container).className).toContain('pr-state--merged')
  })
})

describe('讲英文', () => {
  it('同一个词在这一屏也是那一份英文，一个汉字都不剩', async () => {
    setLocale('en')
    const { container, rerender } = mount({ phase: 'working' })
    expect(badge(container).textContent?.trim()).toBe('Building')
    expect(CJK.test(badge(container).textContent ?? '')).toBe(false)

    await rerender({ topic, members: [], me: 'ligan', connected: true, focus: false, phase: 'reviewing' })
    expect(badge(container).textContent?.trim()).toBe('In review')

    await rerender({ topic, members: [], me: 'ligan', connected: true, focus: false, phase: 'delivering' })
    expect(badge(container).textContent?.trim()).toBe('Delivering')
  })

  it('归档的话题念的是 Accepted，不是 Archived', () => {
    setLocale('en')
    const { container } = mount({ topic: { ...topic, status: 'archived' } })
    // 看板那一列的 archived 是「已归档 / Archived」。同一个标识符，两个意思，
    // 所以是两个键——这条守住别被「顺手合并」掉。
    expect(badge(container).textContent?.trim()).toBe('Accepted')
  })
})

describe('切一次语言', () => {
  it('已经画出来的徽章当场跟着换', async () => {
    setLocale('zh-CN')
    const { container } = mount({ phase: 'working' })
    expect(badge(container).textContent?.trim()).toBe('施工中')

    setLocale('en')
    await vi.waitFor(() => expect(badge(container).textContent?.trim()).toBe('Building'))
    expect(CJK.test(badge(container).textContent ?? '')).toBe(false)
  })
})
