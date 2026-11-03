<template>
  <!-- The inbox page's activity feed: things that happened, done once read. A
       notification calling the person to act (a question, an invitation, a review)
       is not here: the thing itself waits in the list above and leaves when handled
       (backend `NotificationRepository._in_feed`). Grouped by day, newest first. -->
  <section class="notification-feed">
    <header class="notification-feed__head">
      <h2 class="notification-feed__title">{{ t('home.inbox.feed') }}</h2>
      <div class="notification-feed__controls">
        <v-switch
          v-model="unreadOnly"
          :label="t('notifications.common.unreadOnly')"
          color="primary"
          density="compact"
          hide-details
          inset
          class="notification-feed__unread"
        />
        <BaseButton v-if="hasUnread" size="sm" @click="markAllAsRead">
          {{ t('notifications.common.markAllAsRead') }}
        </BaseButton>
      </div>
    </header>
    <!-- Activity failed to load: replace this block in place with an error and a
         retry (docs/design-system.md §3.10). It used to only console.error and
         leave a blank, indistinguishable from "no notifications". -->
    <BaseLoadError
      v-if="failed"
      class="notification-feed__load-error"
      :title="t('notifications.common.loadFailed')"
      :error="errorReason || null"
      @retry="reload"
    />
    <!-- First load still in flight: the row shape is predictable, so draw a row
         skeleton instead of leaving a blank. -->
    <LoadingSkeleton v-else-if="loading && !notifications.length" variant="list" class="notification-feed__skel" />
    <div v-else-if="notifications.length > 0" class="notification-feed__list">
      <!-- New ones arrived while reading: say so on top instead of pushing the rows down. -->
      <div v-if="fresh > 0" class="notification-feed__fresh">
        <BaseButton size="sm" prepend-icon="mdi-arrow-up" @click="showFresh">
          {{ t('notifications.common.newItems', { count: fresh }) }}
        </BaseButton>
      </div>
      <template v-for="day in days" :key="day.key">
        <h3 class="notification-feed__day">{{ day.label }}</h3>
        <NotificationItem
          v-for="notification in day.items"
          :key="notification.id"
          :notification="notification"
          :time="clockTime(notification.createdAt)"
          :on-mark-as-read="markAsRead"
          :on-delete="deleteNotification"
        />
      </template>
      <!-- Loading the next page failed: keep the pages we already have, append the
           error and a retry under them, and do not blank the feed. -->
      <BaseLoadError
        v-if="moreFailed"
        class="notification-feed__load-error"
        :title="t('notifications.common.loadFailed')"
        :error="errorReason || null"
        @retry="loadMore"
      />
      <div v-else-if="hasMore" class="notification-feed__more">
        <BaseButton size="sm" :loading="loading" @click="loadMore">
          {{ t('notifications.common.loadMore') }}
        </BaseButton>
      </div>
    </div>
    <p v-else-if="!loading" class="notification-feed__quiet t-body">
      {{ unreadOnly ? t('notifications.common.noUnread') : t('notifications.common.noNotifications') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import type { Notification } from '@/types/apiNotifications'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useEventListener } from '@vueuse/core'

import { useUnreadNotifications } from '@/composables/useUnreadNotifications'

import { NotificationsApi } from '@/api/notifications'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import NotificationItem from '@/components/common/Notification/NotificationItem.vue'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { dayKey, dayLabel } from '@/lib/chatGrouping'
import { clockTime } from '@/lib/dateText'
import { onInboxChange } from '@/lib/inboxLive'
import { keys } from '@/query/keys'
import { fromSnapshot } from '@/query/snapshot'

const { t } = useI18n()

const unread = useUnreadNotifications()

// 「只看未读」记在这台浏览器上，下次打开还是上次那样。
const UNREAD_ONLY_KEY = 'cheesex.notificationFeed.unreadOnly'
const unreadOnly = ref(storageGet(UNREAD_ONLY_KEY) === '1')

const notifications = ref<Notification[]>([])
const loading = ref(false)
// 动态没读出来：就地显示错误 + 重试，而不是留一片空白装作「暂无通知」。
const failed = ref(false)
// 翻页（「加载更多」）失败：已经到手的那几页不能扔，只在列表下面就地接错误 + 重试
// ——整块换成失败会把看得好好的那几页一起弄没（同搜索页，docs/design-system.md §3.10）。
const moreFailed = ref(false)
const errorReason = ref('')
const cursorStart = ref<string | undefined>(undefined)
const pageSize = ref(20)
const hasMore = ref(false)

// 判断是否有未读通知
const hasUnread = computed(() => notifications.value.some((notification) => !notification.read))

// 按本地日期分组，新的在前（服务端给的就是这个顺序）。读的人在哪个时区，「今天」就是
// 那个时区的今天，和对话里的日期线同一个说法（`lib/chatGrouping.ts`）。
const days = computed(() => {
  const groups: { key: string; label: string; items: Notification[] }[] = []
  for (const notification of notifications.value) {
    // 一条时间读不出来的也得落在某一天，不能让整块因此画不出来。
    const at = Number.isFinite(notification.createdAt) ? notification.createdAt : 0
    const iso = new Date(at).toISOString()
    const key = dayKey(iso)
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.items.push(notification)
    else groups.push({ key, label: dayLabel(iso), items: [notification] })
  }
  return groups
})

/** 这一次要什么：「只看未读」时只要没读的，否则全要。 */
const readFilter = () => (unreadOnly.value ? false : undefined)

// 获取通知列表。每换一次筛选 generation 加一：换之前发出、换之后才回来的那一页属于
// 上一种筛选，丢掉，不然「只看未读」里会混进读过的。
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
    const read = () =>
      NotificationsApi.list({
        read: readFilter(),
        pageStart: cursorStart.value,
        pageSize: pageSize.value,
      })
    const page =
      !isMore && cursorStart.value === undefined
        ? await fromSnapshot(keys.inboxNotifications(unreadOnly.value), read)
        : await read()

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

watch(unreadOnly, (value) => {
  // 存不进去就这一次有效。
  storageSet(UNREAD_ONLY_KEY, value ? '1' : '0')
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

    // 「只看未读」时读掉的那条也先留在原处，免得正在读的几行跳动；下次重新打开再按
    // 筛选取。
    const notification = notifications.value.find((n) => n.id === notificationId)
    if (notification) notification.read = true

    fetchUnreadCount()
  } catch (error) {
    console.error('标记通知已读失败:', error)
  }
}

// 标记所有通知为已读
const markAllAsRead = async () => {
  try {
    await NotificationsApi.markAllAsRead()

    notifications.value.forEach((notification) => {
      notification.read = true
    })

    fetchUnreadCount()
  } catch (error) {
    console.error('标记所有通知已读失败:', error)
  }
}

// 删除通知
const deleteNotification = async (notificationId: number) => {
  try {
    await NotificationsApi.del(notificationId)

    notifications.value = notifications.value.filter((n) => n.id !== notificationId)

    fetchUnreadCount()
  } catch (error) {
    console.error('删除通知失败:', error)
  }
}

// 读完、删完之后把首页那一格的小点对上。
const fetchUnreadCount = () => unread.refresh()

// 列表上面那几行之后又来了几条。看第一页就够：新的一定排在最前。
const fresh = ref(0)

// 有没有新的：问一遍第一页，比列表最上面那一条新的就是。列表还空着就直接摆上来——
// 没有正在读的东西可打断。
async function lookForNew() {
  if (loading.value || failed.value) return
  const mine = generation
  try {
    const page = await NotificationsApi.list({ read: readFilter(), pageSize: pageSize.value })
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

onMounted(() => {
  fetchNotifications()
})
</script>

<style scoped>
.notification-feed__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin: 32px 0 8px;
}
.notification-feed__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.notification-feed__controls {
  display: flex;
  gap: 12px;
  align-items: center;
}
.notification-feed__unread :deep(.v-label) {
  color: var(--muted);
  font-size: 13px;
  opacity: 1;
}
.notification-feed__fresh {
  display: flex;
  justify-content: center;
  padding: 8px;
  border-bottom: 1px solid var(--line);
}
.notification-feed__list {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
}
/* 一天的小标题。紧跟在它下面的那一行不再画顶上那条线：标题本身就把两天分开了。 */
.notification-feed__day {
  margin: 0;
  padding: 12px 16px 4px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}
.notification-feed__day + :deep(.inbox-row) {
  border-top: none;
}
/* 首次加载的骨架坐在和真实列表同一张卡里，行到齐时这一块不换高度。 */
.notification-feed__skel {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
  padding: 8px 12px;
}
.notification-feed__load-error {
  padding: 8px 0;
}
.notification-feed__more {
  display: flex;
  justify-content: center;
  padding: 8px;
  border-top: 1px solid var(--line);
}
.notification-feed__quiet {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--muted);
}
</style>
