// 「动态」那一块的判断：取数、分档、翻页、有没有新的，以及服务端说「来了一行」之后
// 怎么补问。这些以前全在 `NotificationFeed.vue` 里，那一件因此既画画面又自己取数
// （C 档）：待办页想只收 props 单独渲染它做不到。现在判断住在这里，画面只吃 props、
// 只发事件；谁调这一份（`views/InboxView.vue` 那只容器）谁把结果递下去。
//
// 原样搬过来的一条都不改：停在哪一档记在浏览器里、换档时 `generation` 加一挡掉慢回来
// 的上一页、翻页失败只接在列表下面、来了新的先问一句顶上再说。理由都还写在各自那一段。
import type { NotificationCategory } from '@/components/common/Notification/categories'
import type { Notification } from '@/types/apiNotifications'

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { useUnreadNotifications } from '@/composables/useUnreadNotifications'

import { NotificationsApi } from '@/api/notifications'
import { NOTIFICATION_CATEGORIES, typesOfCategory } from '@/components/common/Notification/categories'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { onInboxChange } from '@/lib/inboxLive'

// 停在哪一档记在这台浏览器上，下次打开还是上次那样。读过的那几档（「全部」「未读」
// 是上一版的写法）不认识了就回「需我处理」。
const FILTER_KEY = 'cheesex.notificationFeed.filter'
function readFilter(): NotificationCategory {
  const saved = storageGet(FILTER_KEY)
  return NOTIFICATION_CATEGORIES.find((category) => category === saved) ?? 'mine'
}

export function useNotificationFeed() {
  const unread = useUnreadNotifications()

  const filter = ref<NotificationCategory>(readFilter())

  // 每一档各有多少条。载荷里带「全部 12 条」那个数就是它；删掉一条之后要重新问一遍。
  const counts = ref<Record<NotificationCategory, number> | null>(null)

  // 三档的数字各问一次服务端（一组类型只要一条，拿的是它的 total）。
  async function refreshCounts() {
    try {
      const [mine, notice, system] = await NotificationsApi.countByTypes(
        NOTIFICATION_CATEGORIES.map((category) => typesOfCategory(category))
      )
      counts.value = { mine, notice, system }
    } catch (error) {
      // 数字取不到不影响读列表：三档的格子先只写名字。
      console.error('获取动态条数失败:', error)
    }
  }

  const notifications = ref<Notification[]>([])
  const loading = ref(false)
  // 动态没读出来：就地显示错误 + 重试，而不是留一片空白装作「暂无通知」。
  const failed = ref(false)
  // 翻页（「加载更多」）失败：已经到手的那几页不能扔，只在列表下面就地接错误 + 重试
  // ——整块换成失败会把看得好好的那几页一起弄没（同搜索页，docs/design-system.md §3.10）。
  const moreFailed = ref(false)
  const errorReason = ref('')
  const cursorStart = ref<string | undefined>(undefined)
  const pageSize = ref(10)
  const hasMore = ref(false)

  // 列表上面那几行之后又来了几条（这一档里的）。看第一页就够：新的一定排在最前。
  const fresh = ref(0)

  // 获取通知列表。每换一次筛选 generation 加一：换之前发出、换之后才回来的那一页
  // 属于上一档，丢掉，不然「需我处理」那一栏会混进别的档的通知。
  let generation = 0
  const fetchNotifications = async (isMore = false) => {
    if (loading.value) return

    const mine = generation
    loading.value = true
    // 只是接着往下翻：不动上面那一段的失败态，这一次失败也不让整块换掉。
    moreFailed.value = false
    if (!isMore) failed.value = false
    errorReason.value = ''
    try {
      const page = await NotificationsApi.list({
        // 只问服务端要这一档的几种事由。已读未读不问服务端：读没读靠行的底色和「标为
        // 已读」区分，不改变这一条属于哪一档。
        type: typesOfCategory(filter.value),
        pageStart: cursorStart.value,
        pageSize: pageSize.value,
      })

      if (mine !== generation) return
      notifications.value = [...notifications.value, ...page.notifications]
      hasMore.value = page.page.hasMore
      cursorStart.value = page.page.nextStart
    } catch (error) {
      console.error('获取通知失败:', error)
      if (mine === generation) {
        if (isMore) moreFailed.value = true
        else failed.value = true
        errorReason.value = error instanceof Error ? error.message : ''
      }
    } finally {
      if (mine === generation) loading.value = false
    }
  }

  // 换筛选就是从第一页重新问一遍：游标是跟着上一种筛选走的，接着往下翻会漏。
  function reload() {
    generation += 1
    fresh.value = 0
    loading.value = false
    cursorStart.value = undefined
    notifications.value = []
    hasMore.value = false
    moreFailed.value = false
    void fetchNotifications()
  }

  watch(filter, (value) => {
    // 存不进去就这一次有效。
    storageSet(FILTER_KEY, value)
    reload()
  })

  // 加载更多通知
  const loadMore = () => {
    void fetchNotifications(true)
  }

  // 标记单个通知为已读
  const markAsRead = async (notificationId: number) => {
    try {
      await NotificationsApi.updateStatus(notificationId, true)

      // 更新本地通知状态。读掉的那条还留在这一档里——它没换事由，只是不再是新的。
      const notification = notifications.value.find((n) => n.id === notificationId)
      if (notification) notification.read = true

      // 更新未读数量
      fetchUnreadCount()
    } catch (error) {
      console.error('标记通知已读失败:', error)
    }
  }

  // 标记所有通知为已读
  const markAllAsRead = async () => {
    try {
      await NotificationsApi.markAllAsRead()

      // 更新本地通知状态
      notifications.value.forEach((notification) => {
        notification.read = true
      })

      // 更新未读数量
      fetchUnreadCount()
    } catch (error) {
      console.error('标记所有通知已读失败:', error)
    }
  }

  // 删除通知
  const deleteNotification = async (notificationId: number) => {
    try {
      await NotificationsApi.del(notificationId)

      // 从列表中移除
      notifications.value = notifications.value.filter((n) => n.id !== notificationId)

      // 更新未读数量；这一档的总数也少了一条。
      fetchUnreadCount()
      void refreshCounts()
    } catch (error) {
      console.error('删除通知失败:', error)
    }
  }

  // 读完、删完之后把首页那一格的小点对上。
  const fetchUnreadCount = () => unread.refresh()

  // 有没有新的：问一遍这一档的第一页，比列表最上面那一条新的就是。列表还空着就直接
  // 摆上来——没有正在读的东西可打断。
  async function lookForNew() {
    void refreshCounts()
    if (loading.value || failed.value) return
    const mine = generation
    try {
      const page = await NotificationsApi.list({ type: typesOfCategory(filter.value), pageSize: pageSize.value })
      if (mine !== generation || loading.value) return
      if (notifications.value.length === 0) {
        notifications.value = page.notifications
        hasMore.value = page.page.hasMore
        cursorStart.value = page.page.nextStart
        return
      }
      const newest = Math.max(...notifications.value.map((n) => n.id))
      fresh.value = page.notifications.filter((n) => n.id > newest).length
    } catch {
      // 只是看一眼有没有新的：没问到就等下一次，列表照旧。
    }
  }

  function showFresh() {
    reload()
    void refreshCounts()
  }

  // 新的一行一来服务端就说（`lib/inboxLive.ts`）。连接断开期间漏掉的，在这一页重新回
  // 到眼前时补问一次；切来切去不必每次都问，隔 30 秒以上才问。
  const LOOK_INTERVAL_MS = 30_000
  let lookedAt = Date.now()
  function lookIfStale() {
    if (document.visibilityState !== 'visible' || Date.now() - lookedAt < LOOK_INTERVAL_MS) return
    lookedAt = Date.now()
    void lookForNew()
  }
  const stopListening = onInboxChange(() => {
    lookedAt = Date.now()
    void lookForNew()
  })
  onBeforeUnmount(stopListening)
  useEventListener(document, 'visibilitychange', lookIfStale)
  useEventListener(window, 'focus', lookIfStale)

  // 初始化加载通知。三档的数字和第一页一起要，格子一出来就带着数。
  onMounted(() => {
    void refreshCounts()
    fetchNotifications()
  })

  return {
    filter,
    counts,
    notifications,
    loading,
    failed,
    moreFailed,
    errorReason,
    hasMore,
    fresh,
    reload,
    loadMore,
    markAsRead,
    markAllAsRead,
    deleteNotification,
    showFresh,
  }
}
