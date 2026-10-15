<template>
  <!-- 待办页的「动态」：提到你、回复你、邀请你、截止提醒。以前它是顶栏铃铛的下拉
       卡片；铃铛拆了，这些东西就住在这里，和「等你处理」一页。

       **只认 props**：这一档有哪些条、三档各多少、翻到哪儿了、要不要顶上那条「有 N 条
       新动态」，全是从外面递进来的；换一档、标已读、删一条、接着往下翻、重来一次，都
       往上发事件。看哪一档、问服务端、存偏好、来了新的怎么补问，都在
       `useNotificationFeed.ts`，`views/InboxView.vue` 那只容器把它接上——于是这一块在
       测试和 /demo 里都只需要一串 props。 -->
  <section class="notification-feed">
    <header class="notification-feed__head">
      <h2 class="notification-feed__title">{{ t('home.inbox.feed') }}</h2>
      <div class="notification-feed__controls">
        <SegmentedControl
          :model-value="filter"
          :options="filterOptions"
          :label="t('notifications.common.filterLabel')"
          size="md"
          @update:model-value="emit('update:filter', $event)"
        />
        <BaseButton v-if="hasUnread" size="sm" @click="emit('mark-all-read')">
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
      @retry="emit('retry')"
    />
    <!-- First load still in flight: the row shape is predictable, so draw a row
         skeleton instead of leaving a blank. -->
    <LoadingSkeleton v-else-if="loading && !notifications.length" variant="list" class="notification-feed__skel" />
    <div v-else-if="notifications.length > 0" class="notification-feed__list">
      <!-- 看着列表时来了新的：不把正在读的那几行往下挤，先在顶上说一声，点了才换成最新的。 -->
      <div v-if="fresh > 0" class="notification-feed__fresh">
        <BaseButton size="sm" prepend-icon="mdi-arrow-up" @click="emit('show-fresh')">
          {{ t('notifications.common.newItems', { count: fresh }) }}
        </BaseButton>
      </div>
      <v-list density="compact" lines="three" class="py-0" bg-color="transparent">
        <!-- 行本身（`NotificationItem`）由外面递进来：它自己会问到后端（那几类邀请的
             接受 / 拒绝是在行里办的），这一块要能只收 props 单独画，就不能把它写死
             在这儿。行为一点没变——递进来的那件照样收到同一串 props，标记已读和删除
             这两只动作由下面那两只只发事件的适配器接。 -->
        <component
          :is="itemComponent"
          v-for="notification in notifications"
          :key="notification.id"
          :notification="notification"
          :category-label="t(CATEGORY_LABEL[notificationCategoryOf(notification.type)])"
          :on-mark-as-read="onMarkAsRead"
          :on-delete="onDelete"
        />
      </v-list>
      <!-- Loading the next page failed: keep the pages we already have, append the
           error and a retry under them, and do not blank the feed. -->
      <BaseLoadError
        v-if="moreFailed"
        class="notification-feed__load-error"
        :title="t('notifications.common.loadFailed')"
        :error="errorReason || null"
        @retry="emit('more')"
      />
      <div v-else-if="hasMore" class="notification-feed__more">
        <BaseButton size="sm" :loading="loading" @click="emit('more')">
          {{ t('notifications.common.loadMore') }}
        </BaseButton>
      </div>
    </div>
    <p v-else-if="!loading" class="notification-feed__quiet t-body">
      {{ t('notifications.common.noNotifications') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import type { NotificationCategory } from '@/components/common/Notification/categories'
import type { Notification } from '@/types/apiNotifications'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import { NOTIFICATION_CATEGORIES, notificationCategoryOf } from '@/components/common/Notification/categories'
import SegmentedControl from '@/components/common/SegmentedControl.vue'

defineOptions({ name: 'NotificationFeed' })

const props = defineProps<{
  /** 现在看的是哪一档。 */
  filter: NotificationCategory
  /** 三档各有多少条；还没问到就是 null，格子先只写名字。 */
  counts: Record<NotificationCategory, number> | null
  /** 这一档当前这一页。 */
  notifications: Notification[]
  /** 正在读这一档。 */
  loading: boolean
  /** 整块读失败：上面那段换成错误 + 重试。 */
  failed: boolean
  /** 只往下翻那一页失败：已经到手的那几页留着，错误接在下面。 */
  moreFailed: boolean
  errorReason: string
  hasMore: boolean
  /** 列表上面那几行之后又来了几条。 */
  fresh: number
  /** 一行长什么样。见模板里那段：行自己会问到后端，所以由外面递进来。 */
  itemComponent: Component
}>()

const emit = defineEmits<{
  'update:filter': [value: NotificationCategory]
  'mark-all-read': []
  'mark-as-read': [notificationId: number]
  delete: [notificationId: number]
  more: []
  retry: []
  'show-fresh': []
}>()

const { t } = useI18n()

// 三档各自的词条（`notifications.category.*`）。写在这里而不是拼字符串，是为了
// catalog 的「每个键都有调用处」那一关看得见它们。
const CATEGORY_LABEL: Record<NotificationCategory, string> = {
  mine: 'notifications.category.mine',
  notice: 'notifications.category.notice',
  system: 'notifications.category.system',
}

// 三档的格子：有数字就带上「· 12」，没有就先只写名字。
const filterOptions = computed(() =>
  NOTIFICATION_CATEGORIES.map((category) => ({
    value: category,
    label: props.counts ? `${t(CATEGORY_LABEL[category])} · ${props.counts[category]}` : t(CATEGORY_LABEL[category]),
  }))
)

// 还有没读的：「全部标为已读」那颗只有这时候才画。
const hasUnread = computed(() => props.notifications.some((notification) => !notification.read))

// 行的两只动作只往上发事件（`NotificationItem` 收的是函数 props，不是事件）。
const onMarkAsRead = (notificationId: number) => emit('mark-as-read', notificationId)
const onDelete = (notificationId: number) => emit('delete', notificationId)
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
