// 项目框架（频道清单、未读、提醒档位）不定时去问：项目里没有动静就一次也不问，有了
// 动静由项目推送说一声，变了的那一份当场重读。
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'

const store = vi.hoisted(() => ({ openProject: vi.fn(), error: null }))
// 这一份只管外框读什么、什么时候读，所以路由是个哑的：跳转计数接管它那两个函数，空闲
// 预热问到几个页名时一个都找不到（`getRoutes` 空表），两件事都安静地什么都不做。
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), getRoutes: () => [] }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: {} }), useRouter: () => router }))
vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => '1' }))
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
const feed = vi.hoisted(() => ({
  channels: [] as {
    topic: string
    onopen: (() => void) | null
    onmessage: ((e: { data: string }) => void) | null
    close: () => void
    closed: boolean
  }[],
}))
vi.mock('@/lib/roomLink', () => ({
  resetRoomLink: () => {},
  openRoomChannel: (topic: string) => {
    const channel = {
      topic,
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      readyState: 1,
      closed: false,
      send: () => {},
      drop: () => {},
      close() {
        channel.closed = true
      },
    }
    feed.channels.push(channel)
    return channel
  },
}))
import ProjectShell from './ProjectShell.vue'

import { seedProject } from '@/test/seedQueries'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  feed.channels.length = 0
  for (const fn of Object.values(api)) fn.mockClear()
})

function mount() {
  // 进来时手上已经是刚读过的一份：这一刻不用问。
  seedProject('p', { topics: [], unread: {}, privateUnread: {}, notifyLevels: {} }, 'alice')
  return render(ProjectShell, {
    props: { projectId: 'p' },
    global: { plugins: [createPinia()], stubs: { RouterView: true, VSnackbar: true } },
  })
}

it('asks nothing while nothing in the project changes', async () => {
  vi.useFakeTimers()
  const view = mount()
  await vi.advanceTimersByTimeAsync(5 * 60_000)
  expect(api.listTopics).not.toHaveBeenCalled()
  expect(api.getTopicUnread).not.toHaveBeenCalled()
  view.unmount()
})

it('reads the unread marks again the moment the project says they changed', async () => {
  const view = mount()
  const channel = feed.channels.find((c) => c.topic === 'project:p')!
  channel.onmessage?.({ data: JSON.stringify({ type: 'state', resource: 'unread', id: 'r1' }) })
  await new Promise((r) => setTimeout(r, 0))
  expect(api.getTopicUnread).toHaveBeenCalledTimes(1)
  view.unmount()
})

it('stops listening once the reader leaves the project', async () => {
  const view = mount()
  const channel = feed.channels.find((c) => c.topic === 'project:p')!
  view.unmount()
  expect(channel.closed).toBe(true)
})
