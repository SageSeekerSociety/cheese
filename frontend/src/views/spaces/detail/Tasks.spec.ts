// 题目列表顶上的「全部 / 我参与的 / 我发布的」。守的是四条规矩：
//
// 1. 选「我参与的」只列我领过的题：向列表要的是 joined=true，不是在本地筛一页。
// 2. 这一格写在地址里（?filter=），和分类（?category=）一起留着，换一格不丢另一格。
// 3. 「我发布的」要看得到还没过审的题：通用列表只给已通过的，所以这一格读的是
//    「我发布的题目」接口，那里的待审核题目得出现在屏幕上。
// 4. 「只看待处理」只留有报名待审核或提交待评审的题，也写在地址里。
//
// 另有两条筛选：话题可以多选，列表要的是「带其中任一个」；搜索按回车才查。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const taskList = vi.fn()
const myPublished = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: { list: (...a: unknown[]) => taskList(...a) },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getSpaceTopics: async () => ({
      data: {
        topics: [
          { id: 3, name: '深度学习' },
          { id: 4, name: '计算机视觉' },
        ],
      },
    }),
    listCategories: async () => ({
      data: { categories: [{ id: 7, name: '第 1 章', displayOrder: 0, archivedAt: null }] },
    }),
    getMyPublishedTasks: (...a: unknown[]) => myPublished(...a),
    listAnnouncements: async () => ({ data: { current: [], expired: [], notifyCount: null } }),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import Tasks from './Tasks.vue'

import i18n, { setLocale } from '@/i18n'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11
const Blank = defineComponent({ render: () => h('div') })

function task(id: number, name: string) {
  return {
    id,
    name,
    intro: '',
    topics: [],
    joined: false,
    approved: 'APPROVED',
    deadline: null,
    participantLimit: 0,
    participants: { total: 0, examples: [] },
    creator: { id: 1, username: 'alice', nickname: 'Alice' },
    createdAt: 1,
  }
}

function publishedTask(taskId: number, taskName: string, overrides: Record<string, unknown> = {}) {
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
    ...overrides,
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

  it('「只看待处理」只留有待评审提交的题', async () => {
    myPublished.mockImplementation(async () => ({
      data: {
        tasks: [
          publishedTask(5, '没有待处理的题', { approved: 'APPROVED', visibilityStatus: 'APPROVED_VISIBLE' }),
          publishedTask(6, '有提交待评审的题', { approved: 'APPROVED', pendingReviewCount: 1 }),
        ],
      },
    }))
    const router = await mount({ filter: 'publishing' })
    await waitFor(() => expect(screen.getByText('没有待处理的题')).toBeTruthy())

    await fireEvent.click(screen.getByText('只看待处理'))

    await waitFor(() => expect(router.currentRoute.value.query).toMatchObject({ filter: 'publishing', pending: '1' }))
    await waitFor(() => expect(screen.queryByText('没有待处理的题')).toBeNull())
    expect(screen.getByText('有提交待评审的题')).toBeTruthy()
  })

  it('待评审的提交带出题人去「领取者」页签处理', async () => {
    myPublished.mockImplementation(async () => ({
      data: { tasks: [publishedTask(6, '有提交待评审的题', { approved: 'APPROVED', pendingReviewCount: 2 })] },
    }))
    const router = await mount({ filter: 'publishing' })

    await fireEvent.click(await screen.findByText(/2 个待评审/))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('TasksParticipants'))
    expect(router.currentRoute.value.params).toMatchObject({ taskId: '6' })
  })
})

describe('题目列表的话题与搜索', () => {
  // 话题下拉是一个 VOverlay，happy-dom 没有 visualViewport，不补上菜单打不开。
  beforeAll(() => {
    vi.stubGlobal('visualViewport', {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      addEventListener() {},
      removeEventListener() {},
    })
    vi.stubGlobal('devicePixelRatio', 1)
  })
  afterAll(() => vi.unstubAllGlobals())

  beforeEach(() => {
    setLocale('zh-CN')
    taskList.mockImplementation(async () => ({
      data: { tasks: [task(1, '一道题')], page: { hasMore: false, nextStart: null } },
    }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('话题可以多选，取消全部选择后不再按话题筛', async () => {
    await mount()
    await waitFor(() => expect(screen.getByText('一道题')).toBeTruthy())

    await fireEvent.click(screen.getByRole('button', { name: /全部话题/ }))
    await fireEvent.click(await screen.findByText('深度学习'))
    await fireEvent.click(screen.getByText('计算机视觉'))

    await waitFor(() => expect(taskList).toHaveBeenLastCalledWith(expect.objectContaining({ topics: [3, 4] })))

    await fireEvent.click(screen.getByText('清除选择'))
    await waitFor(() => expect(taskList).toHaveBeenLastCalledWith(expect.objectContaining({ topics: undefined })))
  })

  it('搜索按回车才查', async () => {
    await mount()
    await waitFor(() => expect(taskList).toHaveBeenCalled())
    const calls = taskList.mock.calls.length

    const box = screen.getByRole('searchbox')
    await fireEvent.update(box, '风格迁移')
    expect(taskList.mock.calls.length).toBe(calls)

    await fireEvent.submit(box.closest('form')!)
    await waitFor(() => expect(taskList).toHaveBeenLastCalledWith(expect.objectContaining({ keywords: '风格迁移' })))
  })
})
