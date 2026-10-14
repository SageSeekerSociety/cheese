// 答案卡片只画状态、把要点的事交上去：采纳、投票（赞同/反对/撤回）、收藏都发成事件，
// 请求和「这个人能不能采纳」的判据在页面那边（`composables/useAnswerActions`，另有一
// 份测它）。这一份从卡片这一侧验接缝：点下去交上去的是哪件事、带上的是哪一份答案、
// 采纳那颗按钮在谁手里才出现。
import type { Answer, Group, Question, User } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 卡片不再碰 `@/api`，但 `@/utils/parser` 那条链会走到 `@/i18n`，这里只把 `useI18n`
// 换成「原样返回 key」的那一个（InvitationList.test.ts 同款）。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AnswerCard from '../AnswerCard.vue'

const ASKER: User = {
  id: 7,
  username: 'fulu',
  nickname: '福禄',
  avatarId: 3,
  intro: '在做题',
  question_count: 1,
  answer_count: 1,
}

const ANSWER_ID = 11
const QUESTION_ID = 42

function makeAnswer(over: Partial<Answer> = {}): Answer {
  return {
    id: ANSWER_ID,
    question_id: QUESTION_ID,
    content: JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: '因为瑞利散射。' } }] }),
    author: ASKER,
    created_at: 0,
    updated_at: 0,
    attitudes: { positive_count: 2, negative_count: 0, difference: 2 },
    is_favorite: false,
    comment_count: 0,
    favorite_count: 0,
    view_count: 0,
    is_group: false,
    // 要验的不是小组问答，把这一格顶掉。
    group: null as unknown as Group,
    ...over,
  }
}

function makeQuestion(over: Partial<Question> = {}): Question {
  return {
    id: QUESTION_ID,
    title: '为什么天空是蓝的',
    content: '',
    author: ASKER,
    type: 0,
    topics: [],
    created_at: 0,
    updated_at: 0,
    attitudes: { positive_count: 0, negative_count: 0, difference: 0 },
    is_follow: false,
    is_answered: false,
    answer_count: 1,
    comment_count: 0,
    follow_count: 0,
    view_count: 0,
    is_group: false,
    has_bounty: false,
    bounty: 0,
    bounty_start_at: 0,
    is_solved: false,
    ...over,
  }
}

function mountCard(
  over: {
    answer?: Answer
    question?: Question
    canAccept?: boolean
    defaultAvatarId?: number | null
  } = {}
) {
  const answer = over.answer ?? makeAnswer()
  const result = render(AnswerCard, {
    props: {
      answer,
      question: over.question ?? makeQuestion(),
      canAccept: over.canAccept ?? false,
      defaultAvatarId: over.defaultAvatarId ?? null,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { ...result, answer }
}

/** `t` 在这一份里被换成了「原样返回 key」的那一个，所以按钮就靠 key 认。 */
const byText = (where: Element, text: string) =>
  [...where.querySelectorAll('button')].find((b) => b.textContent?.includes(text))

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

describe('答案卡片上的动作', () => {
  it('点赞交上去的是「赞同」和这份答案，画面上的分数是给进来的那一份', async () => {
    const { container, answer, emitted } = mountCard()
    expect(container.querySelector('.voter')!.textContent).toContain('2')

    await fireEvent.click(container.querySelector('.voter button')!)

    expect(emitted().upvote).toEqual([[answer]])
  })

  it('收藏那颗按钮跟着「收没收藏」换字，点一下把这件事交上去', async () => {
    const favorited = mountCard({ answer: makeAnswer({ is_favorite: true }) })
    expect(byText(favorited.container, 'questions.detail.buttons.unfavorite')).toBeTruthy()

    const plain = mountCard()
    const toFavorite = byText(plain.container, 'questions.detail.buttons.favorite')!
    await fireEvent.click(toFavorite)
    expect(plain.emitted().favorite).toEqual([[plain.answer]])
  })

  it('采纳那颗按钮只在「这个人能采纳」时出现，点了交上去的是这份答案', async () => {
    const other = mountCard({ canAccept: false })
    expect(byText(other.container, 'questions.detail.buttons.accept')).toBeUndefined()

    const mine = mountCard({ canAccept: true })
    const accept = byText(mine.container, 'questions.detail.buttons.accept')!
    expect(accept).toBeTruthy()

    await fireEvent.click(accept)
    expect(mine.emitted().accept).toEqual([[mine.answer]])
  })

  it('已经采纳过的题不再画那颗按钮', () => {
    const { container } = mountCard({
      canAccept: true,
      question: makeQuestion({ accepted_answer: makeAnswer({ id: 12 }) }),
    })
    expect(byText(container, 'questions.detail.buttons.accept')).toBeUndefined()
  })

  it('头像用的是他自己挑的那张；用的就是全局默认那张时不画图', () => {
    const chosen = mountCard({ defaultAvatarId: 1 })
    expect(chosen.container.querySelector('.v-avatar img')).toBeTruthy()

    const borrowed = mountCard({ defaultAvatarId: ASKER.avatarId })
    expect(borrowed.container.querySelector('.v-avatar img')).toBeNull()
  })
})
