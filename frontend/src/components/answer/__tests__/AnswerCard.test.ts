// 答案卡片上要发请求的一共三件事：采纳、投票、收藏；还有一件要读身份的——「这个人
// 能不能采纳」。四样都搬进了 `composables/useAnswerActions`，卡片只画它给的状态。
// 这一份从卡片这一侧验接缝：点下去发的是哪一趟请求、服务端回来的分数落在画面哪儿、
// 采纳那颗按钮在谁手里才出现。
import type { Answer, Group, Question, User } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  postAttitude: vi.fn(),
  favorite: vi.fn(),
  unfavorite: vi.fn(),
  acceptAnswer: vi.fn(),
}))

vi.mock('@/network/api/answers', () => ({
  AnswersApi: {
    postAttitude: (...args: unknown[]) => mocks.postAttitude(...args),
    favorite: (...args: unknown[]) => mocks.favorite(...args),
    unfavorite: (...args: unknown[]) => mocks.unfavorite(...args),
  },
}))
vi.mock('@/network/api/questions', () => ({
  QuestionApi: { acceptAnswer: (...args: unknown[]) => mocks.acceptAnswer(...args) },
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
// 只换掉 `useI18n`，其余照旧：`@/composables/useAnswerActions` 那条 import 链会走到
// `@/i18n/index`，那里要的是真的 `createI18n`（InvitationList.test.ts 同款）。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AnswerCard from '../AnswerCard.vue'

import AccountService from '@/services/account'

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

function mountCard(over: { answer?: Answer; question?: Question } = {}) {
  return render(AnswerCard, {
    props: { answer: over.answer ?? makeAnswer(), question: over.question ?? makeQuestion() },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** `t` 在这一份里被换成了「原样返回 key」的那一个，所以按钮就靠 key 认。 */
const byText = (where: Element, text: string) =>
  [...where.querySelectorAll('button')].find((b) => b.textContent?.includes(text))

beforeEach(() => {
  mocks.postAttitude.mockReset().mockResolvedValue({
    data: { attitudes: { positive_count: 3, negative_count: 0, difference: 3 } },
  })
  mocks.favorite.mockReset().mockResolvedValue({ data: null })
  mocks.unfavorite.mockReset().mockResolvedValue({ data: null })
  mocks.acceptAnswer.mockReset().mockResolvedValue({ data: null })
  AccountService.user = null
})

afterEach(() => {
  AccountService.user = null
  cleanup()
})

describe('答案卡片上的动作', () => {
  it('点赞发的是这道题这份答案的 POSITIVE，服务端回来的分数当场落在画面上', async () => {
    const { container } = mountCard()
    // 投票器里第一颗是赞同。
    const up = container.querySelector('.voter button')!
    expect(container.querySelector('.voter')!.textContent).toContain('2')

    await fireEvent.click(up)

    expect(mocks.postAttitude).toHaveBeenCalledWith(QUESTION_ID, ANSWER_ID, 'POSITIVE')
    await waitFor(() => expect(container.querySelector('.voter')!.textContent).toContain('3'))
  })

  it('收藏点一下发 favorite，再点一下发 unfavorite', async () => {
    const { container } = mountCard()
    const toFavorite = byText(container, 'questions.detail.buttons.favorite')!
    await fireEvent.click(toFavorite)
    expect(mocks.favorite).toHaveBeenCalledWith(QUESTION_ID, ANSWER_ID)

    // 收没收藏是这份数据自己带的：点完它就变成「取消收藏」。
    await waitFor(() => expect(byText(container, 'questions.detail.buttons.unfavorite')).toBeTruthy())
    await fireEvent.click(byText(container, 'questions.detail.buttons.unfavorite')!)
    expect(mocks.unfavorite).toHaveBeenCalledWith(QUESTION_ID, ANSWER_ID)
  })

  it('采纳那颗按钮只有提问的人看得到，点了才发采纳', async () => {
    AccountService.user = { ...ASKER, id: 8 } as never
    const other = mountCard()
    expect(byText(other.container, 'questions.detail.buttons.accept')).toBeUndefined()

    AccountService.user = { ...ASKER } as never
    const mine = mountCard()
    const accept = byText(mine.container, 'questions.detail.buttons.accept')!
    expect(accept).toBeTruthy()

    await fireEvent.click(accept)
    expect(mocks.acceptAnswer).toHaveBeenCalledWith(QUESTION_ID, ANSWER_ID)
  })

  it('已经采纳过的题不再画那颗按钮', () => {
    AccountService.user = { ...ASKER } as never
    const { container } = mountCard({ question: makeQuestion({ accepted_answer: makeAnswer({ id: 12 }) }) })
    expect(byText(container, 'questions.detail.buttons.accept')).toBeUndefined()
  })
})
