/** 待办页的「动态」那一块：以前读失败只 console.error，页面上留一片空白，和「暂无
 *  通知」分不出来。这里锁住它的四种状态各自说自己的话——行还在路上时先画骨架、
 *  读失败时**就地**换成错误 + 重试、本来就没有时说「暂无通知」、重试真的再问一遍。
 */
import type { NotificationType } from '@/types/apiNotifications'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const list = vi.fn()
const countByTypes = vi.fn<(groups: NotificationType[][]) => Promise<number[]>>(async () => [0, 0, 0])
const refresh = vi.fn()
vi.mock('@/api/notifications', () => ({
  NotificationsApi: {
    list: (...args: unknown[]) => list(...args),
    countByTypes: (groups: NotificationType[][]) => countByTypes(groups),
    getUnreadCount: async () => ({ count: 0 }),
    updateStatus: vi.fn(),
    markAllAsRead: vi.fn(),
    del: vi.fn(),
  },
}))
vi.mock('@/composables/useUnreadNotifications', () => ({
  useUnreadNotifications: () => ({ count: { value: 0 }, refresh, set: vi.fn() }),
}))

// 服务端那条连接换成一个能在测试里拨动的开关：拨一下就是「收件箱多了一行」。
const inboxListeners = new Set<() => void>()
vi.mock('@/lib/inboxLive', () => ({
  onInboxChange: (listener: () => void) => {
    inboxListeners.add(listener)
    return () => inboxListeners.delete(listener)
  },
}))
function somethingNewArrives() {
  for (const listener of inboxListeners) listener()
}

import NotificationFeed from './NotificationFeed.vue'

import i18n, { setLocale, t } from '@/i18n'

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  list.mockReset()
  countByTypes.mockReset()
  countByTypes.mockResolvedValue([0, 0, 0])
  refresh.mockReset()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function emptyPage() {
  return { notifications: [], page: { hasMore: false, nextStart: undefined } }
}

async function mount() {
  return render(NotificationFeed, {
    global: { plugins: [vuetify, i18n], stubs: { NotificationItem: true } },
  })
}

describe('待办页「动态」这一块', () => {
  it('首次加载还在路上时先画行的骨架，不留一片空白', async () => {
    const pending = deferred<ReturnType<typeof emptyPage>>()
    list.mockReturnValueOnce(pending.promise)

    await mount()

    expect(screen.getByRole('status')).toBeTruthy()

    pending.resolve(emptyPage())
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
  })

  it('读失败时就地显示原因和重试，而不是装作「暂无通知」', async () => {
    list.mockRejectedValueOnce(new Error('服务器错误'))

    await mount()

    expect(await screen.findByText(t('notifications.common.loadFailed'))).toBeTruthy()
    expect(screen.getByText('服务器错误')).toBeTruthy()
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()
    expect(screen.queryByText(t('notifications.common.noNotifications'))).toBeNull()
  })

  it('点重试真的再问一遍服务端', async () => {
    list.mockRejectedValueOnce(new Error('服务器错误'))
    await mount()
    await screen.findByText(t('notifications.common.loadFailed'))
    expect(list).toHaveBeenCalledTimes(1)

    list.mockResolvedValueOnce(emptyPage())
    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText(t('notifications.common.loadFailed'))).toBeNull())
  })

  it('本来就没有通知时说「暂无通知」，不带语气终止标点', async () => {
    list.mockResolvedValueOnce(emptyPage())

    await mount()

    expect(await screen.findByText(t('notifications.common.noNotifications'))).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  // 「加载更多」失败：已经到手的那几页不能扔——整块换成失败会把看得好好的通知一起弄没。
  // 错误只接在列表下面，重试接着翻，不把整块换掉（同搜索页 §3.10）。
  it('加载更多失败：到手的那几页留着，错误接在下面，重试接着翻', async () => {
    list.mockResolvedValueOnce({
      notifications: [
        { id: 1, read: false },
        { id: 2, read: false },
      ],
      page: { hasMore: true, nextStart: 'c1' },
    })
    const { container } = await mount()
    await waitFor(() => expect(container.querySelectorAll('notification-item-stub').length).toBe(2))

    list.mockRejectedValueOnce(new Error('翻页失败'))
    await fireEvent.click(screen.getByRole('button', { name: t('notifications.common.loadMore') }))

    expect(await screen.findByText('翻页失败')).toBeTruthy()
    // 到手的两条还在，失败没有把整块换掉。
    expect(container.querySelectorAll('notification-item-stub').length).toBe(2)
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()

    list.mockResolvedValueOnce({
      notifications: [{ id: 3, read: false }],
      page: { hasMore: false, nextStart: undefined },
    })
    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    await waitFor(() => expect(container.querySelectorAll('notification-item-stub').length).toBe(3))
    await waitFor(() => expect(screen.queryByText('翻页失败')).toBeNull())
  })

  // 「动态」按事由分档：默认停在「需我处理」，一格上的数字是那一档的总条数。筛选交给
  // 服务端做（分页和 total 都是它算的），所以这里要确认问出去的正是这一档的几种事由。
  it('默认停在「需我处理」，三档各带着自己的条数，问服务端要的就是这一档的事由', async () => {
    countByTypes.mockResolvedValue([3, 7, 2])
    list.mockResolvedValueOnce(emptyPage())

    await mount()

    expect(await screen.findByRole('radio', { name: `${t('notifications.category.mine')} · 3` })).toBeTruthy()
    expect(screen.getByRole('radio', { name: `${t('notifications.category.notice')} · 7` })).toBeTruthy()
    expect(screen.getByRole('radio', { name: `${t('notifications.category.system')} · 2` })).toBeTruthy()

    const asked = list.mock.calls[0][0] as { type: string[] }
    expect(asked.type).toContain('CHEESE_QUESTION')
    expect(asked.type).not.toContain('MENTION')
  })

  it('换一档就从第一页重问，而且要的是那一档的事由', async () => {
    countByTypes.mockResolvedValue([3, 7, 2])
    list.mockResolvedValue(emptyPage())

    await mount()
    await fireEvent.click(await screen.findByRole('radio', { name: `${t('notifications.category.notice')} · 7` }))

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    const asked = list.mock.calls[1][0] as { type: string[]; pageStart?: string }
    expect(asked.type).toContain('MENTION')
    expect(asked.type).not.toContain('CHEESE_QUESTION')
    // 换了档就从第一页来：游标是跟着上一档走到底的，接着往下翻会漏。
    expect(asked.pageStart).toBeUndefined()
  })

  // 看着列表时来了新的：不把正在读的那几行往下挤，顶上说一声「有 N 条新动态」，点了
  // 才换成最新的。
  it('来了新的先在顶上说一声，点了才换成最新的', async () => {
    list.mockResolvedValueOnce({ notifications: [{ id: 5, read: true }], page: { hasMore: false } })
    const { container } = await mount()
    await waitFor(() => expect(container.querySelectorAll('notification-item-stub').length).toBe(1))

    list.mockResolvedValue({
      notifications: [
        { id: 7, read: false },
        { id: 6, read: false },
        { id: 5, read: true },
      ],
      page: { hasMore: false },
    })
    somethingNewArrives()

    const fresh = await screen.findByRole('button', { name: t('notifications.common.newItems', { count: 2 }) })
    // 正在读的那一行没被挤走，新的也还没塞进来。
    expect(container.querySelectorAll('notification-item-stub').length).toBe(1)

    await fireEvent.click(fresh)

    await waitFor(() => expect(container.querySelectorAll('notification-item-stub').length).toBe(3))
    expect(screen.queryByRole('button', { name: t('notifications.common.newItems', { count: 2 }) })).toBeNull()
  })

  it('说有变化、这一档里却没有新的时，什么也不提示', async () => {
    list.mockResolvedValue({ notifications: [{ id: 5, read: true }], page: { hasMore: false } })
    const { container } = await mount()
    await waitFor(() => expect(container.querySelectorAll('notification-item-stub').length).toBe(1))

    somethingNewArrives()

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('button', { name: /动态/ })).toBeNull()
  })

  it('列表本来是空的，来了新的就直接摆上来', async () => {
    list.mockResolvedValueOnce(emptyPage())
    const { container } = await mount()
    await screen.findByText(t('notifications.common.noNotifications'))

    list.mockResolvedValue({ notifications: [{ id: 9, read: false }], page: { hasMore: false } })
    somethingNewArrives()

    await waitFor(() => expect(container.querySelectorAll('notification-item-stub').length).toBe(1))
  })
})
