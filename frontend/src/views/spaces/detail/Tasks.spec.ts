// 题目列表顶上的「全部 / 我参与的 / 我发布的」。守的是三条规矩：
//
// 1. 选「我参与的」只列我领过的题：向列表要的是 joined=true，不是在本地筛一页。
// 2. 这一格写在地址里（?filter=），和分类（?category=）一起留着，换一格不丢另一格。
// 3. 「我发布的」要看得到还没过审的题：通用列表只给已通过的，所以这一格读的是
//    「我发布的题目」接口，那里的待审核题目得出现在屏幕上。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const taskList = vi.fn()
const myPublished = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: { list: (...a: unknown[]) => taskList(...a) },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getSpaceTopics: async () => ({ data: { topics: [] } }),
    listCategories: async () => ({
      data: { categories: [{ id: 7, name: '第 1 章', displayOrder: 0, archivedAt: null }] },
    }),
    getMyPublishedTasks: (...a: unknown[]) => myPublished(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 卡片本身不是这里要测的：只画题目名。
vi.mock('@/components/TaskCard.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    __esModule: true,
    default: defineComponent({
      props: { task: { type: Object, required: true } },
      setup: (props) => () => h('div', (props.task as { name: string }).name),
    }),
  }
})

import Tasks from './Tasks.vue'

import i18n, { setLocale } from '@/i18n'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11
const Blank = defineComponent({ render: () => h('div') })

function task(id: number, name: string) {
  return { id, name, topics: [], joined: false }
}

function publishedTask(taskId: number, taskName: string) {
  return {
    taskId,
    taskName,
    category: { id: 7, name: '第 1 章' },
    approved: 'NONE',
    visibilityStatus: 'PENDING_APPROVAL',
    isVisible: false,
    createdAt: 1,
    publishedAt: null,
    participantLimit: null,
    participantCount: 0,
    approvedParticipantCount: 0,
    pendingParticipantApprovalCount: 0,
    submittedParticipantCount: 0,
    pendingReviewCount: 0,
    successfulParticipantCount: 0,
    failedParticipantCount: 0,
    submissionConversionRate: 0,
    successRate: 0,
  }
}

async function mount(query: Record<string, string> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: Blank as Component },
      { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: Blank as Component },
      { path: '/spaces/:spaceId/tasks/:taskId/participants', name: 'TasksParticipants', component: Blank },
      { path: '/spaces/:spaceId/tasks/:taskId/submissions', name: 'TasksSubmissions', component: Blank },
    ],
  })
  await router.push({ name: 'SpacesDetailTasksList', params: { spaceId: String(SPACE_ID) }, query })
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSpaceStore()
  store.currentSpaceId = SPACE_ID
  render(Tasks, { global: { plugins: [createVuetify({ components, directives }), router, pinia, i18n] } })
  return router
}

describe('题目列表的范围', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    taskList.mockImplementation(async (params: { joined?: boolean }) => ({
      data: {
        tasks: params.joined ? [task(2, '我领过的题')] : [task(1, '别人的题'), task(2, '我领过的题')],
        page: { hasMore: false, nextStart: null },
      },
    }))
    myPublished.mockImplementation(async () => ({ data: { tasks: [publishedTask(5, '还在审核的题')] } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('选「我参与的」只列我领过的题，分类留在地址里', async () => {
    const router = await mount({ category: '7' })
    await waitFor(() => expect(screen.getByText('别人的题')).toBeTruthy())

    await fireEvent.click(screen.getByText('我参与的'))

    await waitFor(() =>
      expect(router.currentRoute.value.query).toMatchObject({ filter: 'participating', category: '7' })
    )
    await waitFor(() => expect(screen.queryByText('别人的题')).toBeNull())
    expect(screen.getByText('我领过的题')).toBeTruthy()
    expect(taskList).toHaveBeenLastCalledWith(expect.objectContaining({ joined: true, categoryId: 7 }))
  })

  it('「全部」不按参与筛', async () => {
    await mount()
    await waitFor(() => expect(taskList).toHaveBeenCalled())
    for (const [params] of taskList.mock.calls) expect((params as { joined?: boolean }).joined).toBeUndefined()
  })

  it('「我发布的」列出还没过审的题', async () => {
    await mount({ filter: 'publishing' })
    await waitFor(() => expect(screen.getByText('还在审核的题')).toBeTruthy())
    expect(myPublished).toHaveBeenCalledWith(SPACE_ID, expect.anything())
  })

  it('从「我发布的」切回「全部」，地址里只剩分类', async () => {
    const router = await mount({ filter: 'publishing', category: '7' })
    await waitFor(() => expect(screen.getByText('还在审核的题')).toBeTruthy())

    await fireEvent.click(screen.getByText('全部'))

    await waitFor(() => expect(router.currentRoute.value.query).toEqual({ category: '7' }))
    await waitFor(() => expect(screen.getByText('别人的题')).toBeTruthy())
  })
})
