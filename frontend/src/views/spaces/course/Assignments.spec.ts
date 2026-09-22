// 课程的「作业与验收」（老师那一屏）：
//   1. 默认进来看的是**验收队列**（`reviewed=false`），不是全部提交；
//   2. 一屏看到「谁的哪份作业」—— 名字来自名册、作业来自行自己带的 taskTitle；
//   3. 四个数字来自服务端同一口径，拿不到就整块不出现（不编 0）；
//   4. 切到「全部」时不再带那个过滤。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const getSubmissionQueue = vi.fn()
const listMembers = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getSubmissionQueue: (...a: unknown[]) => getSubmissionQueue(...a),
    listMembers: (...a: unknown[]) => listMembers(...a),
  },
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

vi.mock('@/services/account', async () => {
  const actual = await vi.importActual<typeof import('@/services/account')>('@/services/account')
  return { ...actual, default: { _user: { value: { id: 1 } } } }
})

import Assignments from './Assignments.vue'

const ROW = {
  id: 11,
  taskId: 3,
  taskTitle: '第 3 周作业',
  participantId: 7,
  version: 2,
  createdAt: Date.UTC(2026, 8, 22, 4, 0),
  updatedAt: Date.UTC(2026, 8, 22, 4, 0),
  member: { id: 5, intro: '', name: '', avatarId: 0 },
  submitter: { id: 5, username: 'alice', nickname: 'Alice', avatarId: null, intro: null },
  content: [],
  review: { reviewed: false },
}

const SUMMARY = { participants: 3, submissions: 2, pendingReview: 1, missing: 1 }

const MEMBERS = {
  members: [{ userId: 5, joinedAt: 0, user: { id: 5, username: 'alice', nickname: 'Alice' } }],
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
      { path: '/spaces/:spaceId', name: 'SpacesDetail', component: { template: '<div />' } },
      {
        path: '/spaces/:spaceId/tasks/publish',
        name: 'SpacesDetailPublishTask',
        component: { template: '<div />' },
      },
    ],
  })
  // 这一页在 mount 那一刻就要读 :spaceId，路由必须先就位。
  await router.push('/spaces/9')
  await router.isReady()
  return render(Assignments, { global: { plugins: [vuetify, router, createPinia()] } })
}

beforeAll(() => {
  vi.stubGlobal('visualViewport', { width: 1280, height: 800, addEventListener() {}, removeEventListener() {} })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('作业与验收', () => {
  it('默认要的是验收队列，不是全部提交', async () => {
    getSubmissionQueue.mockResolvedValue({
      data: { submissions: [ROW], summary: SUMMARY, page: { total: 1 } },
    })
    listMembers.mockResolvedValue({ data: MEMBERS })

    await mountPage()
    await waitFor(() => expect(getSubmissionQueue).toHaveBeenCalled())

    const [, params] = getSubmissionQueue.mock.calls[0]
    expect(params.reviewed).toBe(false)
  })

  it('一屏看到谁的哪份作业：名字取自名册，作业名来自行本身', async () => {
    getSubmissionQueue.mockResolvedValue({
      data: { submissions: [ROW], summary: SUMMARY, page: { total: 1 } },
    })
    listMembers.mockResolvedValue({ data: MEMBERS })

    const { findByText, getByText } = await mountPage()
    await flush()

    expect(await findByText('Alice')).toBeTruthy()
    expect(getByText('第 3 周作业')).toBeTruthy()
    expect(getByText('spaces.course.assignments.statePending')).toBeTruthy()
  })

  it('四个数字照服务端的口径显示，没交的人数不是猜的', async () => {
    getSubmissionQueue.mockResolvedValue({
      data: { submissions: [ROW], summary: SUMMARY, page: { total: 1 } },
    })
    listMembers.mockResolvedValue({ data: MEMBERS })

    const { container, findByText } = await mountPage()
    await flush()

    // 报名 3 / 已交 2 / 等你看 1 / 还没交 1，四格按这个顺序
    const values = Array.from(container.querySelectorAll('.stat .text-h5')).map((el) => el.textContent?.trim())
    expect(values).toEqual(['3', '2', '1', '1'])
    expect(await findByText('spaces.course.assignments.statMissing')).toBeTruthy()
  })

  it('队列空时说的是「还没有人交」，并给出发布作业的出口', async () => {
    getSubmissionQueue.mockResolvedValue({
      data: { submissions: [], summary: SUMMARY, page: { total: 0 } },
    })
    listMembers.mockResolvedValue({ data: MEMBERS })

    const { findByText } = await mountPage()
    await flush()

    expect(await findByText('spaces.course.assignments.emptyTitle')).toBeTruthy()
  })
})
