import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'

const store = vi.hoisted(() => ({ openProject: vi.fn(), error: null }))
// 这一份只管轮询，所以路由是个哑的：跳转计数接管它那两个函数，空闲预热问到几个页名时
// 一个都找不到（`getRoutes` 空表），两件事都安静地什么都不做。
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), getRoutes: () => [] }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: {} }), useRouter: () => router }))
vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => '1' }))
// 外框轮询的是这个项目的频道清单和未读：数一数它们各被问了几次。
const api = vi.hoisted(() => ({
  listTopics: vi.fn(async () => ({ data: [], total: 0 })),
  getTopicUnread: vi.fn(async () => ({})),
  getPrivateUnread: vi.fn(async () => ({})),
  getTopicNotifyLevels: vi.fn(async () => ({})),
}))
vi.mock('@/api/topics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/topics')>()),
  listTopics: api.listTopics,
}))
vi.mock('@/api/topicReads', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/topicReads')>()),
  getTopicUnread: api.getTopicUnread,
  getPrivateUnread: api.getPrivateUnread,
  getTopicNotifyLevels: api.getTopicNotifyLevels,
}))
import ProjectShell from './ProjectShell.vue'

import { seedProject } from '@/test/seedQueries'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

let visibility: DocumentVisibilityState = 'visible'
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })

// 标签页切前后台：浏览器在 document 上发 visibilitychange，一路冒泡到 window。
function setVisible(visible: boolean) {
  visibility = visible ? 'visible' : 'hidden'
  document.dispatchEvent(new Event('visibilitychange', { bubbles: true }))
}

it('pauses hidden tab polling and refreshes when the reader returns', async () => {
  vi.useFakeTimers()
  visibility = 'hidden'
  // 进来时手上已经是刚读过的一份：这一刻不用问。
  seedProject('p', { topics: [], unread: {}, privateUnread: {}, notifyLevels: {} }, 'alice')
  const view = render(ProjectShell, {
    props: { projectId: 'p' },
    global: { plugins: [createPinia()], stubs: { RouterView: true, VSnackbar: true } },
  })
  await vi.advanceTimersByTimeAsync(90_000)
  expect(api.listTopics).not.toHaveBeenCalled()
  expect(api.getTopicUnread).not.toHaveBeenCalled()

  setVisible(true)
  await vi.advanceTimersByTimeAsync(0)
  expect(api.listTopics).toHaveBeenCalledTimes(1)
  expect(api.getTopicUnread).toHaveBeenCalledTimes(1)

  // 离开了这个项目：再切前后台、再等多久都不再问。
  view.unmount()
  setVisible(false)
  setVisible(true)
  await vi.advanceTimersByTimeAsync(90_000)
  expect(api.listTopics).toHaveBeenCalledTimes(1)
  expect(api.getTopicUnread).toHaveBeenCalledTimes(1)
})

it('polls every 30 seconds while the tab is in front', async () => {
  vi.useFakeTimers()
  visibility = 'visible'
  seedProject('p', { topics: [], unread: {}, privateUnread: {}, notifyLevels: {} }, 'alice')
  const view = render(ProjectShell, {
    props: { projectId: 'p' },
    global: { plugins: [createPinia()], stubs: { RouterView: true, VSnackbar: true } },
  })
  await vi.advanceTimersByTimeAsync(29_000)
  expect(api.listTopics).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2_000)
  expect(api.listTopics).toHaveBeenCalledTimes(1)
  expect(api.getTopicUnread).toHaveBeenCalledTimes(1)
  view.unmount()
})
