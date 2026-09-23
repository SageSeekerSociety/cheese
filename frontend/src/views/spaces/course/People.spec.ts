// 「学生与分组」（老师）那一屏：名单、分组、还没进组的人。
//
// 这一份钉三件事：
//   1. 服务端给什么人、什么组，屏上就出现什么人、什么组 —— 前端不再自己算一遍；
//   2. 还没有组的学生单独列出来（老师要找的是他们）；
//   3. 读不到（不是管理员、或者板没过审）时是空的一屏，不炸、也不编数据。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getCourseRoster = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getCourseRoster: (...a: unknown[]) => getCourseRoster(...a),
    getMyCourseGroup: vi.fn(),
  },
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import People from './People.vue'

const ALICE = { id: 11, username: 'alice', nickname: 'Alice' }
const BOB = { id: 12, username: 'bob', nickname: 'Bob', avatarId: 7 }

function roster(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      students: [
        {
          user: ALICE,
          projects: [{ id: 'p-1', name: '猜数字游戏', teamId: 5 }],
          teamIds: [5],
        },
        { user: BOB, projects: [], teamIds: [] },
      ],
      teams: [{ id: 5, name: '第一组', members: [ALICE] }],
      ...overrides,
    },
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
      { path: '/spaces/:spaceId', name: 'SpacesDetail', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/course/people', name: 'SpacesCoursePeople', component: { template: '<div />' } },
    ],
  })
  // 这一页按 `route.params.spaceId` 取数，所以路由先落定再挂载 —— 不然组件看到的
  // 是 undefined，它就会安安静静地什么都不查（那是它的正常分支，不是 bug）。
  await router.push('/spaces/1/course/people')
  await router.isReady()
  return render(People, { global: { plugins: [vuetify, router, createPinia()] } })
}

beforeAll(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
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
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  getCourseRoster.mockReset()
})

afterEach(cleanup)

describe('students and groups', () => {
  it('shows the groups the server reports, with their members', async () => {
    getCourseRoster.mockResolvedValue(roster())
    const page = await mountPage()
    await waitFor(() => expect(page.getByText('第一组')).toBeTruthy())
    expect(getCourseRoster).toHaveBeenCalledWith(1)
    // 成员画在组里（Alice），人数与组名一起出现。
    expect(page.getByText('Alice')).toBeTruthy()
  })

  it('lists the students who are in no group yet', async () => {
    getCourseRoster.mockResolvedValue(roster())
    const page = await mountPage()
    await waitFor(() => expect(page.getByText('Bob')).toBeTruthy())
    // Bob 没有项目也没有组：他落在「还没有组的学生」那一块，项目位置写的是空态。
    expect(page.getByText('spaces.course.people.noProject')).toBeTruthy()
  })

  it('says everyone is grouped when nobody is missing', async () => {
    getCourseRoster.mockResolvedValue(
      roster({
        students: [
          {
            user: ALICE,
            projects: [{ id: 'p-1', name: '猜数字游戏', teamId: 5 }],
            teamIds: [5],
          },
        ],
      })
    )
    const page = await mountPage()
    await waitFor(() => expect(page.getByText('spaces.course.people.allGrouped')).toBeTruthy())
  })

  it('stays empty (not broken) when the roster cannot be read', async () => {
    getCourseRoster.mockRejectedValue(new Error('403'))
    const page = await mountPage()
    await flush()
    // 读不到就是空的：一屏空态，没有崩，也没有编出来的数字。
    expect(page.getByText('spaces.course.people.noGroups')).toBeTruthy()
    expect(page.queryByText('第一组')).toBeNull()
  })
})
