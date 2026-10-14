/**
 * 队列里的一行（`AdminQueueRow`，§5.2，61px）。
 *
 * 钉住一件事：**作者在行上是一张脸 + 昵称，不是又一个读数。** 优先级、状态、编号、
 * 时间都是关于这条卡片的读数，而「谁提的」是另一个人 —— 那正是二十行下「指派给了谁」
 * 那一格回答的同一个问题。服务端的卡片上就带着 `author_name` / `author_avatar_id` /
 * `author_is_agent`，所以这里不掉成 handle、也不按 handle 长相猜队友。
 *
 * 断言只对着看得见的东西（脸的元素、名字的文字），不替 store 或 api 造替身：这一层只
 * 吃 props，给它 mock 一层才是不测 —— 「props 进来之后画错了」这个失败模式恰好会被
 * 替身盖住。
 */
import type { Component } from 'vue'
import type { FeedbackCard } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it } from 'vitest'

import AdminQueueRow from './AdminQueueRow.vue'

import i18n, { setLocale } from '@/i18n'

/** 一条形状齐全的卡片。作者那四格是这几条用例的主角，其余给一个能画出来的最小形状。 */
function card(over: Record<string, unknown> = {}) {
  return {
    id: 'fb-3',
    display_id: 'FB-1040',
    title: '导出报表偶发 502',
    status: 'received',
    priority: 'normal',
    created_at: '2026-09-19T10:00:00Z',
    last_activity_at: '2026-09-19T10:00:00Z',
    supports: 2,
    comments: 0,
    author_handle: 'maxiaoyu',
    author_name: 'Max',
    author_avatar_id: 7,
    author_is_agent: false,
    assignee_handle: null,
    assignee_name: null,
    assignee_avatar_id: null,
    assignee_is_agent: false,
    ...over,
  } as unknown as FeedbackCard
}

function mountRow(item: FeedbackCard) {
  const vuetify = createVuetify({ components, directives })
  return render(AdminQueueRow as unknown as Component, {
    props: { item, active: false, showStatusWord: false },
    global: { plugins: [vuetify, createPinia(), i18n] },
  })
}

/** meta 行里作者那一张脸。找不到就是没画。 */
function authorChip(container: Element) {
  return container.querySelector('.qrow__meta .identity-view')
}

beforeAll(() => {
  setLocale('zh-CN')
})

describe('管理端反馈队列行 · 作者身份', () => {
  it('作者画的是脸 + 昵称，昵称用的是服务端给的那一个', () => {
    const { container } = mountRow(card({ author_name: 'Max', author_handle: 'maxiaoyu' }))
    const chip = authorChip(container)
    expect(chip, '作者那一格没画身份').not.toBeNull()
    expect(chip!.querySelector('.identity-view__name')?.textContent).toBe('Max')
  })

  it('作者自己挑过的头像走素材 URL', () => {
    const { container } = mountRow(card({ author_avatar_id: 7 }))
    expect(authorChip(container)!.querySelector('img')?.getAttribute('src')).toContain('/avatars/7')
  })

  it('作者没挑头像时画的是按 handle 派生的彩色首字母', () => {
    const { container } = mountRow(card({ author_avatar_id: null }))
    const chip = authorChip(container)!
    expect(chip.querySelector('img')).toBeNull()
    expect(chip.querySelector('.user-avatar-char')).not.toBeNull()
  })

  it('作者是 AI 队友时画的是队友那一张脸 —— 且这是服务端说的，不是按 handle 猜的', () => {
    const { container } = mountRow(card({ author_handle: 'cheese-9f21', author_name: 'Cedar', author_is_agent: true }))
    const chip = authorChip(container)!
    expect(chip.querySelector('.cheese-avatar'), '队友没画超椭圆').not.toBeNull()
    expect(chip.querySelector('.user-avatar-char')).toBeNull()
  })

  it('昵称缺失时退回 handle，脸照样画', () => {
    const { container } = mountRow(card({ author_name: null, author_handle: 'maxiaoyu' }))
    const chip = authorChip(container)!
    expect(chip.querySelector('.identity-view__name')?.textContent).toBe('maxiaoyu')
  })
})
