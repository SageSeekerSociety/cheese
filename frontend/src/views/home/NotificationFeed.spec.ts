/* eslint-disable vue/one-component-per-file -- 测试里要同时摆下宿主与那一页 */
/** 待办页的「动态」那一块：以前是一整件自己取数、自己记偏好的 C 级组件，读失败只
 *  console.error，页面上留一片空白，和「暂无通知」分不出来。现在判断在
 *  `useNotificationFeed.ts` 里、画面只吃 props —— 这一份用一个宿主把两半接起来，锁住
 *  它四种状态各自说自己的话（行还在路上时先画骨架、读失败时**就地**换成错误 + 重试、
 *  本来就没有时说「暂无通知」、重试真的再问一遍），以及换档、翻页、来了新的这几条。
 *
 *  宿主就是 `views/InboxView.vue` 里那几行连线的写照：判断那一半（`useNotificationFeed`）
 *  在宿主里跑，画面只收结果、只往上发事件。行是 `NotificationItem` 本身，它画的是一颗
 *  真行（自带头像、时间、通用动作），所以这里按它真会画出来的东西认。
 */
import type { NotificationCategory } from '@/components/common/Notification/categories'
import type { Notification, NotificationType } from '@/types/apiNotifications'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const list = vi.fn()
const countByTypes = vi.fn<(groups: NotificationType[][]) => Promise<number[]>>(async () => [0, 0, 0])
const updateStatus = vi.fn()
const markAllAsRead = vi.fn()
const del = vi.fn()
const refresh = vi.fn()
vi.mock('@/api/notifications', () => ({
  NotificationsApi: {
    list: (...args: unknown[]) => list(...args),
    countByTypes: (groups: NotificationType[][]) => countByTypes(groups),
    getUnreadCount: async () => ({ count: 0 }),
    updateStatus: (...args: unknown[]) => updateStatus(...args),
    markAllAsRead: (...args: unknown[]) => markAllAsRead(...args),
    del: (...args: unknown[]) => del(...args),
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
import { useNotificationFeed } from './useNotificationFeed'

import i18n, { setLocale, t } from '@/i18n'

/** 宿主：判断那一半在这里跑，画面只收结果。连线就是 `views/InboxView.vue` 里那一段。
 *  行是 `NotificationItem` 本身：它画的是一颗真行，标为已读 / 删除按它自己那两颗按钮认。 */
const FeedHost = defineComponent({
  name: 'FeedHost',
  setup() {
    const feed = useNotificationFeed()
    return () =>
      h(NotificationFeed, {
        filter: feed.filter.value,
        counts: feed.counts.value,
        notifications: feed.notifications.value,
        loading: feed.loading.value,
        failed: feed.failed.value,
        moreFailed: feed.moreFailed.value,
        errorReason: feed.errorReason.value,
        hasMore: feed.hasMore.value,
        fresh: feed.fresh.value,
        'onUpdate:filter': (value: NotificationCategory) => {
          feed.filter.value = value
        },
        onMarkAllRead: () => void feed.markAllAsRead(),
        onMarkAsRead: (id: number) => void feed.markAsRead(id),
        onDelete: (id: number) => void feed.deleteNotification(id),
        onMore: () => feed.loadMore(),
        onRetry: () => feed.reload(),
        onShowFresh: () => feed.showFresh(),
      })
  },
})

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  // 删除那颗按钮弹的确认框是 Vuetify 的浮层，量位置时会读 visualViewport：happy-dom 没
  // 有这一个，补一个空的就够了（和 NotificationItem.spec 一样）。
  vi.stubGlobal('visualViewport', new EventTarget())
  localStorage.clear()
  list.mockReset()
  countByTypes.mockReset()
  countByTypes.mockResolvedValue([0, 0, 0])
  updateStatus.mockReset()
  markAllAsRead.mockReset()
  del.mockReset()
  refresh.mockReset()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
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

/** 一条通知：`types/apiNotifications.ts` 的 `Notification`。行是 `NotificationItem` 真画出来的，
 *  它要读 `type` / `entities` / `createdAt`，所以不能只给 id 和 read。 */
function notice(id: number, over: Partial<Notification> = {}): Notification {
  return {
    id,
    type: 'MENTION',
    read: false,
    createdAt: Date.now(),
    entities: {},
    contextMetadata: {},
    ...over,
  }
}

async function mount() {
  return render(FeedHost, { global: { plugins: [vuetify, i18n] } })
}

/** 画出来的行数。行是 `NotificationItem` 自己（`.notification-item`），不是替身。 */
const rows = (container: Element) => container.querySelectorAll('.notification-item').length

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
        notice(1),
        notice(2),
      ],
      page: { hasMore: true, nextStart: 'c1' },
    })
    const { container } = await mount()
    await waitFor(() => expect(rows(container)).toBe(2))

    list.mockRejectedValueOnce(new Error('翻页失败'))
    await fireEvent.click(screen.getByRole('button', { name: t('notifications.common.loadMore') }))

    expect(await screen.findByText('翻页失败')).toBeTruthy()
    // 到手的两条还在，失败没有把整块换掉。
    expect(rows(container)).toBe(2)
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()

    list.mockResolvedValueOnce({
      notifications: [notice(3)],
      page: { hasMore: false, nextStart: undefined },
    })
    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    await waitFor(() => expect(rows(container)).toBe(3))
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

  // 换了档之后，上一档那一次请求晚回来：它属于上一档，接上去「需我处理」那一栏里就会混进
  // 别的档的通知。判断那一半拿 `generation` 挡住它（这不是这一件能自己看见的事）。
  it('换档时上一档慢回来的那一页丢掉，不混进这一档', async () => {
    const stale = deferred<ReturnType<typeof emptyPage>>()
    list.mockReturnValueOnce(stale.promise)

    const { container } = await mount()

    list.mockResolvedValueOnce({ notifications: [notice(2)], page: { hasMore: false } })
    await fireEvent.click(await screen.findByRole('radio', { name: t('notifications.category.notice') }))
    await waitFor(() => expect(rows(container)).toBe(1))

    // 上一档那一页现在才回来：id 更大也不进这一档，它的「还能往下翻」也不算数。
    stale.resolve({ notifications: [notice(99)], page: { hasMore: true, nextStart: 'x' } })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    await new Promise((resolve) => setTimeout(resolve))
    expect(rows(container)).toBe(1)
    expect(screen.queryByRole('button', { name: t('notifications.common.loadMore') })).toBeNull()
  })

  it('停在哪一档记在这台浏览器上', async () => {
    list.mockResolvedValue(emptyPage())

    await mount()
    await fireEvent.click(await screen.findByRole('radio', { name: t('notifications.category.system') }))

    await waitFor(() => expect(localStorage.getItem('cheesex.notificationFeed.filter')).toBe('system'))
    const asked = list.mock.calls[list.mock.calls.length - 1][0] as { type: string[] }
    expect(asked.type).toContain('SPACE_ANNOUNCEMENT')
  })

  // 看着列表时来了新的：不把正在读的那几行往下挤，顶上说一声「有 N 条新动态」，点了
  // 才换成最新的。
  it('来了新的先在顶上说一声，点了才换成最新的', async () => {
    list.mockResolvedValueOnce({ notifications: [notice(5, { read: true })], page: { hasMore: false } })
    const { container } = await mount()
    await waitFor(() => expect(rows(container)).toBe(1))

    list.mockResolvedValue({
      notifications: [
        notice(7),
        notice(6),
        notice(5, { read: true }),
      ],
      page: { hasMore: false },
    })
    somethingNewArrives()

    const fresh = await screen.findByRole('button', { name: t('notifications.common.newItems', { count: 2 }) })
    // 正在读的那一行没被挤走，新的也还没塞进来。
    expect(rows(container)).toBe(1)

    await fireEvent.click(fresh)

    await waitFor(() => expect(rows(container)).toBe(3))
    expect(screen.queryByRole('button', { name: t('notifications.common.newItems', { count: 2 }) })).toBeNull()
  })

  it('说有变化、这一档里却没有新的时，什么也不提示', async () => {
    list.mockResolvedValue({ notifications: [notice(5, { read: true })], page: { hasMore: false } })
    const { container } = await mount()
    await waitFor(() => expect(rows(container)).toBe(1))

    somethingNewArrives()

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('button', { name: /动态/ })).toBeNull()
  })

  it('列表本来是空的，来了新的就直接摆上来', async () => {
    list.mockResolvedValueOnce(emptyPage())
    const { container } = await mount()
    await screen.findByText(t('notifications.common.noNotifications'))

    list.mockResolvedValue({ notifications: [notice(9)], page: { hasMore: false } })
    somethingNewArrives()

    await waitFor(() => expect(rows(container)).toBe(1))
  })

  // 这一件从画面上消失时，那条「收件箱多了一行」的监听要跟着放掉：不放，关掉待办页之后
  // 每来一行还会叫醒一个已经不画任何东西的处理器。
  it('这一件收走时放掉那条监听', async () => {
    list.mockResolvedValue(emptyPage())
    const { unmount } = await mount()
    await waitFor(() => expect(inboxListeners.size).toBe(1))

    unmount()

    expect(inboxListeners.size).toBe(0)
  })

  it('标为已读和删除带的是那一条自己的 id', async () => {
    list.mockResolvedValueOnce({ notifications: [notice(7)], page: { hasMore: false } })
    const { container } = await mount()
    await waitFor(() => expect(rows(container)).toBe(1))

    updateStatus.mockResolvedValue({})
    // 行是 `NotificationItem` 自己：标已读那颗按钮在行里，按它的字认。
    await fireEvent.click(await screen.findByRole('button', { name: t('notifications.common.markAsRead') }))
    await waitFor(() => expect(updateStatus).toHaveBeenCalledWith(7, true))
    // 读掉的那条还留在这一档里：它没换事由，只是不再是新的。
    expect(rows(container)).toBe(1)
    expect(refresh).toHaveBeenCalled()

    // 删除是一条不可撤销的动，先弹确认框：确认之后才真的删，带的是这一条自己的 id。
    del.mockResolvedValue({})
    await fireEvent.click(screen.getByRole('button', { name: t('notifications.common.delete') }))
    const dialog = await screen.findByRole('alertdialog')
    expect(del).not.toHaveBeenCalled()
    await fireEvent.click(within(dialog).getByRole('button', { name: t('notifications.common.delete') }))
    await waitFor(() => expect(del).toHaveBeenCalledWith(7))
    await waitFor(() => expect(rows(container)).toBe(0))
  })

  it('「全部标为已读」把这一档的动作交给服务端', async () => {
    list.mockResolvedValueOnce({ notifications: [notice(7)], page: { hasMore: false } })
    const { container } = await mount()
    await waitFor(() => expect(rows(container)).toBe(1))

    markAllAsRead.mockResolvedValue({})
    await fireEvent.click(screen.getByRole('button', { name: t('notifications.common.markAllAsRead') }))

    await waitFor(() => expect(markAllAsRead).toHaveBeenCalledTimes(1))
  })
})
