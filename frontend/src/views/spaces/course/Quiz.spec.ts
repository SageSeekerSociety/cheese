// 小测那一屏。这一份钉三件事：
//   1. **学生的载荷里没有答案键** —— 截止前可以改答案重交，能拿到答案键就等于能抄；
//   2. 交完卷当场看到客观题的分，简答显示「等老师判」；
//   3. 老师那一栏把等着判的简答列出来，判分调的是那一条接口。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listUnits = vi.fn()
const getUnitQuiz = vi.fn()
const submitQuizAttempt = vi.fn()
const gradeQuizAnswer = vi.fn()
const createQuiz = vi.fn()
const updateQuiz = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listUnits: (...a: unknown[]) => listUnits(...a),
    getUnitQuiz: (...a: unknown[]) => getUnitQuiz(...a),
    submitQuizAttempt: (...a: unknown[]) => submitQuizAttempt(...a),
    gradeQuizAnswer: (...a: unknown[]) => gradeQuizAnswer(...a),
    createQuiz: (...a: unknown[]) => createQuiz(...a),
    updateQuiz: (...a: unknown[]) => updateQuiz(...a),
  },
}))

vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: () => ({ wait: () => Promise.resolve(true) }) }),
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import CourseQuiz from './Quiz.vue'

function unit(overrides: Record<string, unknown> = {}) {
  return {
    id: 11,
    spaceId: 7,
    week: 4,
    title: '函数',
    summary: '',
    knowledgePointIds: [],
    materialIds: [],
    assignmentTaskId: null,
    publishedAt: 1_700_000_000_000,
    dueAt: null,
    quizId: 21,
    ...overrides,
  }
}

function quiz(overrides: Record<string, unknown> = {}) {
  return { id: 21, spaceId: 7, unitId: 11, title: '第 4 周小测', dueAt: null, ...overrides }
}

function question(overrides: Record<string, unknown> = {}) {
  return {
    id: 31,
    position: 1,
    kind: 'SINGLE_CHOICE',
    prompt: '哪个是循环？',
    options: ['for', 'if'],
    points: 2,
    ...overrides,
  }
}

async function flush() {
  // Vuetify 的 v-dialog 内容是在打开的下一帧才挂上去的，所以光翻微任务不够。
  for (let i = 0; i < 8; i += 1) {
    await new Promise((r) => requestAnimationFrame(() => r(null)))
    await new Promise((r) => setTimeout(r, 0))
  }
}

async function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'SpacesCourseHome', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/course/quiz', name: 'SpacesCourseQuiz', component: { template: '<div />' } },
    ],
  })
  await router.push('/spaces/7/course/quiz?unit=11')
  await router.isReady()
  return render(CourseQuiz, { global: { plugins: [vuetify, router] } })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // jsdom 没有 visualViewport，而 Vuetify 的 v-overlay（对话框）会读它 ——
  // 不补的话「打开判分框」这一步直接抛错，看着像功能坏了。
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
})

beforeEach(() => {
  listUnits.mockReset().mockResolvedValue({ data: { units: [unit()], canTeach: true } })
  submitQuizAttempt.mockReset()
  gradeQuizAnswer.mockReset().mockResolvedValue({ data: { answer: {} } })
  createQuiz.mockReset()
  updateQuiz.mockReset()
})

afterEach(cleanup)

describe('the student answering', () => {
  it('never receives the answer key, even though it exists on the server', async () => {
    getUnitQuiz.mockResolvedValue({
      data: {
        quiz: quiz(),
        canTeach: false,
        questions: [question()],
        maxScore: 2,
        myAttempt: null,
        myAnswers: [],
      },
    })
    // 老师那份有 answer；学生那份载荷里连这一格都没有（服务端分叉，前端不判权限）。
    const page = await mountPage()
    await flush()

    expect(page.getByText('哪个是循环？')).toBeTruthy()
    expect(page.getByText('for')).toBeTruthy()
    // 这一页根本不渲染答案键那一段。
    expect(page.queryByText(/spaces\.course\.quiz\.key/)).toBeNull()
  })

  it('shows the objective score right after handing in, and waits on the short answer', async () => {
    getUnitQuiz.mockResolvedValue({
      data: {
        quiz: quiz(),
        canTeach: false,
        questions: [
          question(),
          question({ id: 32, position: 2, kind: 'SHORT_ANSWER', prompt: '说说为什么', options: [], points: 5 }),
        ],
        maxScore: 7,
        myAttempt: null,
        myAnswers: [],
      },
    })
    const shortAnswer = question({
      id: 32,
      position: 2,
      kind: 'SHORT_ANSWER',
      prompt: '说说为什么',
      options: [],
      points: 5,
    })
    submitQuizAttempt.mockResolvedValue({
      data: {
        quiz: quiz(),
        canTeach: false,
        questions: [question(), shortAnswer],
        maxScore: 7,
        myAttempt: { id: 41, userId: 9, submittedAt: 1, gradedAt: null, score: 2, pendingReview: true },
        myAnswers: [
          { questionId: 31, response: 0, awardedPoints: 2, comment: '', needsReview: false },
          { questionId: 32, response: '因为要先想边界', awardedPoints: null, comment: '', needsReview: true },
        ],
      },
    })

    const page = await mountPage()
    await flush()

    const submit = page.getByText('spaces.course.quiz.submit')
    await fireEvent.click(submit)
    await waitFor(() => expect(submitQuizAttempt).toHaveBeenCalled())
    await flush()

    expect(page.getByText('spaces.course.quiz.pendingReview')).toBeTruthy()
    expect(page.getByText('spaces.course.quiz.awaitingTeacher')).toBeTruthy()
    expect(page.getByText('spaces.course.quiz.got')).toBeTruthy()
    // 截止前还能改：交过卷之后作答项仍然可以动，按钮是「改完再交一次」。
    expect(page.getByText('spaces.course.quiz.resubmit')).toBeTruthy()
    expect(
      page.container.querySelectorAll('.v-input--disabled, .v-selection-control--disabled, [disabled]')
    ).toHaveLength(0)
  })
})

describe('the teacher grading', () => {
  it('lists what is waiting to be judged and grades that one answer', async () => {
    getUnitQuiz.mockResolvedValue({
      data: {
        quiz: quiz(),
        canTeach: true,
        questions: [
          question({
            id: 32,
            kind: 'SHORT_ANSWER',
            prompt: '说说为什么',
            options: [],
            points: 5,
            answer: '要点：边界条件',
          }),
        ],
        maxScore: 5,
        myAttempt: null,
        myAnswers: [],
        submissions: [
          {
            attemptId: 41,
            userId: 9,
            submittedAt: 1,
            gradedAt: null,
            score: 0,
            maxScore: 5,
            user: { id: 9, username: 's', nickname: '小明' },
          },
        ],
        reviewQueue: [
          {
            answerId: 51,
            attemptId: 41,
            userId: 9,
            submittedAt: 1,
            questionId: 32,
            kind: 'SHORT_ANSWER',
            prompt: '说说为什么',
            referenceAnswer: '要点：边界条件',
            points: 5,
            response: '因为要先想边界',
            user: { id: 9, username: 's', nickname: '小明' },
          },
        ],
      },
    })

    const page = await mountPage()
    await flush()

    // 老师的卷面上有答案键，队列里有那一条等着判的简答。
    expect(page.getAllByText(/要点：边界条件/).length).toBeGreaterThan(0)
    expect(page.getByText('因为要先想边界')).toBeTruthy()
    // 复核队列与全班表里各出现一次（同一个人）。
    expect(page.getAllByText('小明').length).toBeGreaterThan(0)

    await fireEvent.click(page.getByText('spaces.course.quiz.grade'))
    await waitFor(() => expect(page.getByText('spaces.course.quiz.form.gradeTitle')).toBeTruthy())
    await fireEvent.click(page.getAllByText('spaces.course.quiz.form.save').at(-1) as HTMLElement)
    await waitFor(() => expect(gradeQuizAnswer).toHaveBeenCalled())
    expect(gradeQuizAnswer.mock.calls[0][0]).toBe(7)
    expect(gradeQuizAnswer.mock.calls[0][2]).toBe(51)
  })

  it('says so plainly when the week has no quiz yet instead of faking a paper', async () => {
    getUnitQuiz.mockResolvedValue({ data: { quiz: null, unitId: 11, canTeach: true } })
    const page = await mountPage()
    await flush()

    expect(page.getByText('spaces.course.quiz.noQuiz')).toBeTruthy()
    expect(page.getByText('spaces.course.quiz.createQuiz')).toBeTruthy()
  })
})
