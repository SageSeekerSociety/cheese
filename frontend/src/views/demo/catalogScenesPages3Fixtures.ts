/**
 * 「拆出来的那半张画面」第二批要的数据：一条回答（正文是服务端存的 Editor.js JSON）、
 * 一道题，以及空间分析那一层的筛选条件。形状照产品里的类型写，不是编的占位。
 */
import type { Component } from 'vue'
import type { Answer, Group, Question, User } from '@/types'
import type { SpaceAnalyticsQueryState } from '@/views/spaces/detail/analytics/utils'

import { defineComponent, h, markRaw } from 'vue'

/** 提问的人：回答卡片上「能不能采纳」认的就是他跟眼下登着的这人是不是同一个。 */
const ASKER: User = {
  id: 7,
  username: 'fulu',
  nickname: '福禄',
  avatarId: 3,
  intro: '在做题',
  question_count: 4,
  answer_count: 9,
}

/** 答案卡片上的正文走 `utils/parser`：它吃 Editor.js 的 JSON 字符串。 */
const BODY = {
  blocks: [
    { type: 'paragraph', data: { text: '因为瑞利散射：短波被大气散射得更厉害。' } },
    { type: 'paragraph', data: { text: '太阳落山时路更长，剩下的就是红的那点光。' } },
  ],
}

export function question(over: Partial<Question> = {}): Question {
  return {
    id: 42,
    title: '为什么天空是蓝的',
    content: JSON.stringify(BODY),
    author: ASKER,
    type: 0,
    topics: [],
    created_at: Date.parse('2026-09-28T10:00:00+08:00') / 1000,
    updated_at: Date.parse('2026-09-28T10:00:00+08:00') / 1000,
    attitudes: { positive_count: 6, negative_count: 0, difference: 6 },
    is_follow: false,
    is_answered: true,
    answer_count: 2,
    comment_count: 1,
    follow_count: 3,
    view_count: 40,
    is_group: false,
    has_bounty: false,
    bounty: 0,
    bounty_start_at: 0,
    is_solved: false,
    ...over,
  }
}

export function answer(over: Partial<Answer> = {}): Answer {
  return {
    id: 11,
    question_id: 42,
    content: JSON.stringify(BODY),
    author: ASKER,
    created_at: Date.parse('2026-09-28T11:00:00+08:00') / 1000,
    updated_at: Date.parse('2026-09-28T11:00:00+08:00') / 1000,
    attitudes: { positive_count: 5, negative_count: 0, difference: 5 },
    is_favorite: false,
    comment_count: 2,
    favorite_count: 1,
    view_count: 0,
    is_group: false,
    // 这些回答不属于任何小组，这一格产品里永远有值，预览站没有小组可给。
    group: null as unknown as Group,
    ...over,
  }
}

/** 列表那一件用的两条：第二条是已经收藏过的。 */
export const ANSWERS: Answer[] = [
  answer(),
  answer({ id: 12, is_favorite: true, attitudes: { positive_count: 1, negative_count: 0, difference: 1 } }),
]

/** 空间分析那一层的分类下拉：`null` 是「全部分类」。 */
export const CATEGORY_ITEMS: Array<{ title: string; value: number | null }> = [
  { title: '全部分类', value: null },
  { title: '第一单元', value: 1 },
  { title: '第二单元', value: 2 },
]

/** 地址上落定的那一份筛选条件。 */
export const ANALYTICS_FILTERS: SpaceAnalyticsQueryState = {
  from: '2026-09-01',
  to: '2026-09-30',
  taskApproved: 'ALL',
  groupBy: 'day',
  realName: 'all',
}

/** 空分析那一层的正文格由路由认出来再递进来；预览站给一个认得出来的替身。 */
export const StubAnalyticsSection: Component = markRaw(
  defineComponent({
    name: 'StubAnalyticsSection',
    setup: () => () => h('div', { class: 't-body' }, '这一格是分析的正文'),
  })
)
