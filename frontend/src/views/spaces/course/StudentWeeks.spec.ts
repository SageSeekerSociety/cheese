// 学生的「本周任务」：和老师的「作业与验收」同一条路由，但学生看的是每一周要做
// 什么 —— 老师那屏的收作业队列接口只给教师，学生打开只会是一张空的教师页。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listUnits = vi.fn()
const getMyParticipations = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listUnits: (...a: unknown[]) => listUnits(...a),
    getMyParticipations: (...a: unknown[]) => getMyParticipations(...a),
  },
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, args?: Record<string, unknown>) => (args ? `${key}:${JSON.stringify(args)}` : key),
    }),
  }
})

import StudentWeeks from './StudentWeeks.vue'

function unit(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    spaceId: 7,
    week: 3,
    title: '循环与数组',
    summary: '',
    knowledgePointIds: [],
    materialIds: [],
    assignmentTaskId: null,
    publishedAt: 1_700_000_000_000,
    dueAt: null,
    ...overrides,
  }
}

async function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'root', component: { template: '<div />' } },
      {
        path: '/spaces/:spaceId/course',
        name: 'SpacesCourseHome',
        component: { template: '<div />' },
      },
      {
        path: '/spaces/:spaceId/course/assignments',
        name: 'SpacesCourseAssignments',
        component: { template: '<div />' },
      },
      { path: '/spaces/:spaceId/course/quiz', name: 'SpacesCourseQuiz', component: { template: '<div />' } },
      {
        path: '/spaces/:spaceId/tasks/:taskId',
        name: 'SpacesDetailTasksDetail',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/spaces/7/course/assignments')
  await router.isReady()
  return render(StudentWeeks, { global: { plugins: [vuetify, router, createPinia()] } })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
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
  getMyParticipations.mockReset().mockResolvedValue({ data: { participations: [] } })
  listUnits.mockReset().mockResolvedValue({ data: { units: [], canTeach: false } })
})

afterEach(cleanup)

describe('本周任务 for a student', () => {
  it('lists every published week, newest first, with the status of each assignment', async () => {
    listUnits.mockResolvedValue({
      data: {
        units: [
          unit({ id: 1, week: 1, title: '变量与输入输出', assignmentTaskId: 41 }),
          unit({ id: 2, week: 2, title: '分支与循环', assignmentTaskId: 42, quizId: 9 }),
        ],
        canTeach: false,
      },
    })
    getMyParticipations.mockResolvedValue({
      data: { participations: [{ participationId: 1, taskId: 41, taskName: '温度换算', completionStatus: 'SUCCESS' }] },
    })
    const page = await mountPage()

    await waitFor(() => expect(page.getByText('分支与循环')).toBeTruthy())
    const weeks = Array.from(page.container.querySelectorAll('[data-week]')).map((el) => el.getAttribute('data-week'))
    expect(weeks).toEqual(['2', '1'])
    expect(page.getByText('spaces.course.weeks.now')).toBeTruthy()
    expect(page.getAllByRole('link', { name: 'spaces.course.weeks.openWork' })).toHaveLength(2)
    expect(page.getByRole('link', { name: 'spaces.course.weeks.openQuiz' })).toBeTruthy()
    expect(page.getByTestId('work-status').textContent).toContain('spaces.course.myCourse.status.success')
  })

  it('says so when nothing has been published', async () => {
    const page = await mountPage()
    await waitFor(() => expect(page.getByText('spaces.course.myCourse.thisWeekEmpty')).toBeTruthy())
  })
})
