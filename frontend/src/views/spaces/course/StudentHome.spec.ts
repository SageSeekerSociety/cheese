// 学生的「我的课程」上那格「本周任务」：接口只把老师**发布过**的单元给他，所以学生
// 这一侧不用再判一次发布状态 —— 但也因此，拿到不止一条时得自己认出「这一周」。
// 这一份钉两件事：
//   1. 多条已发布单元里，显示的是周次最大的那一条
//   2. 这一周有要交的东西时给一个通往它的按钮；没有就不给
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

import StudentHome from './StudentHome.vue'

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
        path: '/spaces/:spaceId/tasks/:taskId',
        name: 'SpacesDetailTasksDetail',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/spaces/7/course')
  await router.isReady()
  return render(StudentHome, { global: { plugins: [vuetify, router, createPinia()] } })
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

describe('this week', () => {
  it('shows the latest published week, and a way to hand its work in', async () => {
    listUnits.mockResolvedValue({
      data: {
        units: [
          unit({ id: 1, week: 2, title: '分支与循环' }),
          unit({ id: 2, week: 3, title: '数组', assignmentTaskId: 42 }),
        ],
        canTeach: false,
      },
    })
    const page = await mountPage()

    await waitFor(() => expect(page.getByText('数组')).toBeTruthy())
    expect(page.queryByText('分支与循环')).toBeNull()
    expect(page.getByText('spaces.course.myCourse.week:{"week":3}')).toBeTruthy()
    expect(page.getByRole('link', { name: 'spaces.course.myCourse.openWork' })).toBeTruthy()
  })

  it('says nothing is handed in this week when the week has no work', async () => {
    listUnits.mockResolvedValue({
      data: { units: [unit({ id: 2, week: 3, title: '数组' })], canTeach: false },
    })
    const page = await mountPage()

    await waitFor(() => expect(page.getByText('数组')).toBeTruthy())
    expect(page.getByText('spaces.course.myCourse.nothingToHandIn')).toBeTruthy()
  })

  it('leaves the week empty when the teacher has published nothing', async () => {
    const page = await mountPage()

    await waitFor(() => expect(page.getByText('spaces.course.myCourse.thisWeekEmpty')).toBeTruthy())
  })
})
