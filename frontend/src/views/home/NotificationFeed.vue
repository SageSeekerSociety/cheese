<template>
  <!-- 待办页的「动态」：提到你、回复你、邀请你、截止提醒。以前它是顶栏铃铛的下拉
       卡片；铃铛拆了，这些东西就住在这里，和「等你处理」一页。 -->
  <section class="notification-feed">
    <header class="notification-feed__head">
      <h2 class="notification-feed__title">{{ t('home.inbox.feed') }}</h2>
      <BaseButton v-if="hasUnread" size="sm" @click="markAllAsRead">
        {{ t('notifications.common.markAllAsRead') }}
      </BaseButton>
    </header>
    <div v-if="notifications.length > 0" class="notification-feed__list">
      <v-list density="compact" lines="three" class="py-0" bg-color="transparent">
        <notification-item
          v-for="notification in notifications"
          :key="notification.id"
          :notification="notification"
          :on-mark-as-read="markAsRead"
          :on-delete="deleteNotification"
        />
      </v-list>
      <div v-if="hasMore" class="notification-feed__more">
        <BaseButton size="sm" :loading="loading" @click="loadMore">
          {{ t('notifications.common.loadMore') }}
        </BaseButton>
      </div>
    </div>
    <p v-else-if="!loading" class="notification-feed__quiet t-body">{{ t('notifications.common.noNotifications') }}</p>
  </section>
</template>

<script setup lang="ts">
import type { Notification } from '@/network/api/notifications/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useUnreadNotifications } from '@/composables/useUnreadNotifications'

import BaseButton from '@/components/base/BaseButton.vue'
import NotificationItem from '@/components/common/Notification/NotificationItem.vue'
import { NotificationsApi } from '@/network/api/notifications'

const { t } = useI18n()

const unread = useUnreadNotifications()

const notifications = ref<Notification[]>([])
const loading = ref(false)
const cursorStart = ref<string | undefined>(undefined)
const pageSize = ref(10)
const hasMore = ref(false)

// 判断是否有未读通知
const hasUnread = computed(() => notifications.value.some((notification) => !notification.read))

// 获取通知列表
const fetchNotifications = async () => {
  if (loading.value) return

  loading.value = true
  try {
    const { data } = await NotificationsApi.list({
      pageStart: cursorStart.value,
      pageSize: pageSize.value,
    })

    notifications.value = [...notifications.value, ...data.notifications]
    hasMore.value = data.page.hasMore
    cursorStart.value = data.page.nextStart
  } catch (error) {
    console.error('获取通知失败:', error)
  } finally {
    loading.value = false
  }
}

// 加载更多通知
const loadMore = () => {
  fetchNotifications()
}

// 标记单个通知为已读
const markAsRead = async (notificationId: number) => {
  try {
    await NotificationsApi.updateStatus(notificationId, true)

    // 更新本地通知状态
    const notification = notifications.value.find((n) => n.id === notificationId)
    if (notification) {
      notification.read = true
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
.notification-feed__list {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
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
