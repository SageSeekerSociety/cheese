/** 待办页的「动态」那一块：以前读失败只 console.error，页面上留一片空白，和「暂无
 *  通知」分不出来。这里锁住它的四种状态各自说自己的话——行还在路上时先画骨架、
 *  读失败时**就地**换成错误 + 重试、本来就没有时说「暂无通知」、重试真的再问一遍。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const list = vi.fn()
const refresh = vi.fn()
vi.mock('@/network/api/notifications', () => ({
  NotificationsApi: {
    list: (...args: unknown[]) => list(...args),
    getUnreadCount: async () => ({ data: { count: 0 } }),
    updateStatus: vi.fn(),
    markAllAsRead: vi.fn(),
    del: vi.fn(),
  },
}))
vi.mock('@/composables/useUnreadNotifications', () => ({
  useUnreadNotifications: () => ({ count: { value: 0 }, refresh, set: vi.fn() }),
}))

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
  return { data: { notifications: [], page: { hasMore: false, nextStart: undefined } } }
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
      data: {
        notifications: [
          { id: 1, read: false },
          { id: 2, read: false },
        ],
        page: { hasMore: true, nextStart: 'c1' },
      },
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
      data: { notifications: [{ id: 3, read: false }], page: { hasMore: false, nextStart: undefined } },
    })
    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    await waitFor(() => expect(container.querySelectorAll('notification-item-stub').length).toBe(3))
    await waitFor(() => expect(screen.queryByText('翻页失败')).toBeNull())
  })
})
