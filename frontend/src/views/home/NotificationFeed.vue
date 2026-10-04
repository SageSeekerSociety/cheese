<template>
  <!-- 待办页的「动态」：提到你、回复你、邀请你、截止提醒。以前它是顶栏铃铛的下拉
       卡片；铃铛拆了，这些东西就住在这里，和「等你处理」一页。 -->
  <section class="notification-feed">
    <header class="notification-feed__head">
      <h2 class="notification-feed__title">{{ t('home.inbox.feed') }}</h2>
      <div class="notification-feed__controls">
        <SegmentedControl
          v-model="filter"
          :options="filterOptions"
          :label="t('notifications.common.filterLabel')"
          size="md"
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
      <v-list density="compact" lines="three" class="py-0" bg-color="transparent">
        <notification-item
          v-for="notification in notifications"
          :key="notification.id"
          :notification="notification"
          :on-mark-as-read="markAsRead"
          :on-delete="deleteNotification"
        />
      </v-list>
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
      {{ filter === 'unread' ? t('notifications.common.noUnread') : t('notifications.common.noNotifications') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import type { Notification } from '@/network/api/notifications/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useUnreadNotifications } from '@/composables/useUnreadNotifications'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import NotificationItem from '@/components/common/Notification/NotificationItem.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { NotificationsApi } from '@/network/api/notifications'

const { t } = useI18n()

const unread = useUnreadNotifications()

// 「全部 / 未读」这个筛选记在这台浏览器上，下次打开还是上次那样。
const FILTER_KEY = 'cheesex.notificationFeed.filter'
type FeedFilter = 'all' | 'unread'
function readFilter(): FeedFilter {
  try {
    return localStorage.getItem(FILTER_KEY) === 'unread' ? 'unread' : 'all'
  } catch {
    return 'all'
  }
}
const filter = ref<FeedFilter>(readFilter())
const filterOptions = computed<ReadonlyArray<{ value: FeedFilter; label: string }>>(() => [
  { value: 'all', label: t('notifications.common.filterAll') },
  { value: 'unread', label: t('notifications.common.filterUnread') },
])

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

// 判断是否有未读通知
const hasUnread = computed(() => notifications.value.some((notification) => !notification.read))

// 获取通知列表。每换一次筛选 generation 加一：换之前发出、换之后才回来的那一页
// 属于上一种筛选，丢掉，不然「未读」那一栏会混进已读的。
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
    const { data } = await NotificationsApi.list({
      // 未读筛选只问服务端要没读的；「全部」不传这一位，行为和从前一样。
      read: filter.value === 'unread' ? false : undefined,
      pageStart: cursorStart.value,
      pageSize: pageSize.value,
    })

    if (mine !== generation) return
    notifications.value = [...notifications.value, ...data.notifications]
    hasMore.value = data.page.hasMore
    cursorStart.value = data.page.nextStart
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
  loading.value = false
  cursorStart.value = undefined
  notifications.value = []
  hasMore.value = false
  moreFailed.value = false
  void fetchNotifications()
}

watch(filter, (value) => {
  try {
    localStorage.setItem(FILTER_KEY, value)
  } catch {
    // 存不进去就这一次有效。
  }
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

    // 更新本地通知状态
    const notification = notifications.value.find((n) => n.id === notificationId)
    if (notification) {
      notification.read = true
      // 正在看未读那一栏：读掉的那条不该再占着一格。
      if (filter.value === 'unread') notifications.value = notifications.value.filter((n) => n.id !== notificationId)
    }

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

    // 未读那一栏被清空了：从服务端再问一遍，而不是留一列刚读掉的行。
    if (filter.value === 'unread') reload()

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

    // 更新未读数量
    fetchUnreadCount()
  } catch (error) {
    console.error('删除通知失败:', error)
  }
}

// 读完、删完之后把首页那一格的小点对上。
const fetchUnreadCount = () => unread.refresh()

// 初始化加载通知
onMounted(() => {
  fetchNotifications()
})
</script>

<style scoped>
.notification-feed__head {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin: 24px 0 8px;
}
.notification-feed__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
/* 筛选在左、批量动作在右：两个都是这一屏的次要动作，贴着标题那一条线。 */
.notification-feed__controls {
  display: flex;
  gap: 8px;
  align-items: center;
}
.notification-feed__list {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
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
  color: var(--faint);
}
</style>
