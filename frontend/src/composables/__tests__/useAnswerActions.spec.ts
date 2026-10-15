// 答案上那几个动作（采纳、三种投票、收藏）的请求都在 `useAnswerActions` 里，卡片只发
// 事件。所以「点下去发的是哪一趟请求、服务端回来的分数落在哪一份数据上」这一份从这里验，
// 按钮长什么样由 `components/answer/__tests__/AnswerCard.test.ts` 那一份管。
import type { PropType } from 'vue'
import type { Answer, Group, Question, User } from '@/types'

import { defineComponent, h } from 'vue'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  postAttitude: vi.fn(),
  favorite: vi.fn(),
  unfavorite: vi.fn(),
  acceptAnswer: vi.fn(),
  success: vi.fn(),
  /** 眼下登着的是谁（`@/services/account` 的 `currentUserId` 读的就是这一格）。 */
  who: { value: undefined as number | undefined },
}))

vi.mock('@/api/answers', () => ({
  AnswersApi: {
    postAttitude: (...args: unknown[]) => mocks.postAttitude(...args),
    favorite: (...args: unknown[]) => mocks.favorite(...args),
    unfavorite: (...args: unknown[]) => mocks.unfavorite(...args),
  },
}))
vi.mock('@/api/questions', () => ({
  QuestionApi: { acceptAnswer: (...args: unknown[]) => mocks.acceptAnswer(...args) },
}))
vi.mock('vuetify-sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mocks.success(...args),
    error: vi.fn(),
  },
}))
vi.mock('@/i18n', () => ({ t: (key: string) => key }))
vi.mock('@/services/account', () => ({ currentUserId: mocks.who }))

import { useAnswerActions } from '../useAnswerActions'

const ASKER_ID = 7
const SOMEONE_ELSE_ID = 8
const ANSWER_ID = 11
const QUESTION_ID = 42

const ASKER: User = {
  id: ASKER_ID,
  username: 'fulu',
  nickname: '福禄',
  avatarId: 3,
  intro: '在做题',
  question_count: 1,
  answer_count: 1,
}

function makeAnswer(over: Partial<Answer> = {}): Answer {
  return {
    id: ANSWER_ID,
    question_id: QUESTION_ID,
    content: '',
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

/** 组合式函数要在组件的 setup 里起，所以给它一个只画按钮的宿主。 */
function mountActions(question: Question | null, answer: Answer) {
  const Host = defineComponent({
    props: {
      question: { type: Object as PropType<Question | null>, default: null },
      answer: { type: Object as PropType<Answer>, required: true },
    },
    setup(props) {
      const actions = useAnswerActions(() => props.question)
      return () =>
        h('div', [
          h('span', { 'data-testid': 'can' }, actions.canAccept.value ? 'yes' : 'no'),
          h('button', { 'data-testid': 'accept', onClick: () => actions.accept(props.answer) }, 'accept'),
          h('button', { 'data-testid': 'up', onClick: () => actions.upvote(props.answer) }, 'up'),
          h('button', { 'data-testid': 'down', onClick: () => actions.downvote(props.answer) }, 'down'),
          h('button', { 'data-testid': 'cancel', onClick: () => actions.cancelVote(props.answer) }, 'cancel'),
          h('button', { 'data-testid': 'fav', onClick: () => actions.favorite(props.answer) }, 'fav'),
        ])
    },
  })
  return render(Host, { props: { question, answer } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.who.value = ASKER_ID
  mocks.postAttitude.mockResolvedValue({ attitudes: { positive_count: 5, negative_count: 0, difference: 5 } })
  mocks.favorite.mockResolvedValue(undefined)
  mocks.unfavorite.mockResolvedValue(undefined)
  mocks.acceptAnswer.mockResolvedValue(undefined)
})
afterEach(cleanup)

describe('答案上的动作', () => {
  it('采纳发的是「接受这一个答案」，题不在手上时不发', async () => {
    const answer = makeAnswer()
    const mine = mountActions(makeQuestion(), answer)
    await fireEvent.click(screen.getByTestId('accept'))
    expect(mocks.acceptAnswer).toHaveBeenCalledWith(QUESTION_ID, ANSWER_ID)
    expect(mocks.success).toHaveBeenCalled()
    mine.unmount()

    vi.clearAllMocks()
    const orphan = makeAnswer()
    mountActions(null, orphan)
    await fireEvent.click(screen.getByTestId('accept'))
    expect(mocks.acceptAnswer).not.toHaveBeenCalled()
  })

  it('三种投票是同一个请求的三个取值，回来的分数写回这份答案', async () => {
    const answer = makeAnswer()
    mountActions(makeQuestion(), answer)

    await fireEvent.click(screen.getByTestId('up'))
    await waitFor(() => expect(answer.attitudes.positive_count).toBe(5))
    expect(mocks.postAttitude).toHaveBeenLastCalledWith(QUESTION_ID, ANSWER_ID, 'POSITIVE')

    await fireEvent.click(screen.getByTestId('down'))
    expect(mocks.postAttitude).toHaveBeenLastCalledWith(QUESTION_ID, ANSWER_ID, 'NEGATIVE')

    await fireEvent.click(screen.getByTestId('cancel'))
    expect(mocks.postAttitude).toHaveBeenLastCalledWith(QUESTION_ID, ANSWER_ID, 'UNDEFINED')
  })

  it('收藏点一下发收藏，再点一下发取消，状态跟着翻', async () => {
    const answer = makeAnswer()
    mountActions(makeQuestion(), answer)

    await fireEvent.click(screen.getByTestId('fav'))
    await waitFor(() => expect(mocks.favorite).toHaveBeenCalledWith(QUESTION_ID, ANSWER_ID))
    expect(answer.is_favorite).toBe(true)

    await fireEvent.click(screen.getByTestId('fav'))
    await waitFor(() => expect(mocks.unfavorite).toHaveBeenCalledWith(QUESTION_ID, ANSWER_ID))
    expect(answer.is_favorite).toBe(false)
  })

  it('只有提问的人能采纳；题不在手上时谁都不能', () => {
    mocks.who.value = ASKER_ID
    const mine = mountActions(makeQuestion(), makeAnswer())
    expect(screen.getByTestId('can').textContent).toBe('yes')
    mine.unmount()

    mocks.who.value = SOMEONE_ELSE_ID
    const others = mountActions(makeQuestion(), makeAnswer())
    expect(screen.getByTestId('can').textContent).toBe('no')
    others.unmount()

    mountActions(null, makeAnswer())
    expect(screen.getByTestId('can').textContent).toBe('no')
  })
})
