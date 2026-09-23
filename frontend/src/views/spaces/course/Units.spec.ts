// 老师的「教学单元」那一屏：这一列单元是课程的时间线，而**发布是这一屏上唯一一个
// 会改变学生看到什么的开关**。这一份钉的就是它：
//   1. 未发布的单元自己标出来 —— 老师得一眼看出哪几周还没放出去
//   2. 点「发布」发的是 published: true，点「撤回发布」发的是 published: false
//   3. 一个单元都没有时给一句话，不给一张看着像出错的空表
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listUnits = vi.fn()
const getMyPublishedTasks = vi.fn()
const updateUnit = vi.fn()
const createUnit = vi.fn()
const deleteUnit = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listUnits: (...a: unknown[]) => listUnits(...a),
    getMyPublishedTasks: (...a: unknown[]) => getMyPublishedTasks(...a),
    updateUnit: (...a: unknown[]) => updateUnit(...a),
    createUnit: (...a: unknown[]) => createUnit(...a),
    deleteUnit: (...a: unknown[]) => deleteUnit(...a),
  },
}))

// 删除要有一次确认；这里一律答「确认」，测的是它确不确认之后的动作。
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: () => ({ wait: () => Promise.resolve(true) }) }),
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import CourseUnits from './Units.vue'

const PUBLISH = 'spaces.course.units.publish'
const UNPUBLISH = 'spaces.course.units.unpublish'

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
    publishedAt: null,
    dueAt: null,
    ...overrides,
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'root', component: { template: '<div />' } },
      {
        path: '/spaces/:spaceId/course/units',
        name: 'SpacesCourseUnits',
        component: { template: '<div />' },
      },
      // 每一行还有一个进这一周小测的入口（`v-btn :to`），路由得存在。
      {
        path: '/spaces/:spaceId/course/quiz',
        name: 'SpacesCourseQuiz',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/spaces/7/course/units')
  await router.isReady()
  return render(CourseUnits, { global: { plugins: [vuetify, router] } })
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
  listUnits.mockReset().mockResolvedValue({ data: { units: [], canTeach: true } })
  getMyPublishedTasks.mockReset().mockResolvedValue({ data: { tasks: [] } })
  updateUnit.mockReset().mockResolvedValue({ data: { unit: unit() } })
  createUnit.mockReset().mockResolvedValue({ data: { unit: unit() } })
  deleteUnit.mockReset().mockResolvedValue({})
})

afterEach(cleanup)

describe('the teacher timeline', () => {
  it('marks the weeks that are still drafts, and the ones already out', async () => {
    listUnits.mockResolvedValue({
      data: {
        units: [
          unit({ id: 1, week: 3, title: '循环与数组', publishedAt: 1_700_000_000_000 }),
          unit({ id: 2, week: 8, title: '递归与分治' }),
        ],
        canTeach: true,
      },
    })
    const page = await mountPage()
    await flush()

    expect(page.getByText('循环与数组')).toBeTruthy()
    expect(page.getByText('递归与分治')).toBeTruthy()
    // 一条已发布、一条还是草稿：各一个徽标，各一个反向的按钮。
    expect(page.getAllByText('spaces.course.units.published')).toHaveLength(1)
    expect(page.getAllByText('spaces.course.units.draft')).toHaveLength(1)
    expect(page.getByRole('button', { name: PUBLISH })).toBeTruthy()
    expect(page.getByRole('button', { name: UNPUBLISH })).toBeTruthy()
  })

  it('publishes a draft week with published: true', async () => {
    listUnits
      .mockResolvedValueOnce({ data: { units: [unit({ id: 9, week: 4 })], canTeach: true } })
      .mockResolvedValue({ data: { units: [unit({ id: 9, week: 4, publishedAt: 1 })], canTeach: true } })
    const page = await mountPage()
    await flush()

    await fireEvent.click(page.getByRole('button', { name: PUBLISH }))
    await flush()

    expect(updateUnit).toHaveBeenCalledWith(7, 9, { published: true })
  })

  it('takes a published week back with published: false', async () => {
    listUnits.mockResolvedValue({
      data: { units: [unit({ id: 5, week: 2, publishedAt: 1_700_000_000_000 })], canTeach: true },
    })
    const page = await mountPage()
    await flush()

    await fireEvent.click(page.getByRole('button', { name: UNPUBLISH }))
    await flush()

    expect(updateUnit).toHaveBeenCalledWith(7, 5, { published: false })
  })

  it('says there is nothing yet instead of drawing an empty table', async () => {
    const page = await mountPage()
    await flush()

    await waitFor(() => expect(page.getByText('spaces.course.units.empty')).toBeTruthy())
  })
})
