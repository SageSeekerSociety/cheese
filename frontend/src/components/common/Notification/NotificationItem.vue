<template>
  <v-list-item
    v-if="hasRouterLink"
    :active="!notification.read"
    :class="{ 'unread-notification': !notification.read }"
    class="notification-item py-2 px-4 transition-fast-in-fast-out"
    @click="navigateToTarget"
  >
    <div class="d-flex align-start w-100">
      <notification-avatar :notification="notification" class="me-3 mt-1" />

      <div class="flex-grow-1 d-flex flex-column">
        <div class="d-flex flex-row justify-space-between align-center notification-item__top">
          <component
            :is="contentComponent"
            ref="contentRef"
            :notification="notification"
            @update-notification="onUpdateNotification"
          />
          <span class="notification-item__time text-caption text-medium-emphasis">{{ formattedTime }}</span>
        </div>

        <!-- Generic row actions (mark read / delete) stay out of the way until the
             pointer hovers the row or keyboard focus lands inside it; touch devices
             have no hover, so they are always shown there. Type-specific actions
             (accept / decline) do not carry this class and stay visible. -->
        <div
          class="d-flex justify-end align-center mt-2"
          :class="{ 'notification-item__actions': !(renderedActions && renderedActions.length > 0) }"
        >
          <template v-if="renderedActions && renderedActions.length > 0">
            <BaseButton
              v-for="(action, index) in renderedActions"
              :key="index"
              :kind="action.color === 'error' ? 'danger' : 'ghost'"
              size="sm"
              density="comfortable"
              class="px-2 ms-2"
              @click.stop="action.handler"
            >
              {{ action.text }}
            </BaseButton>
          </template>
          <template v-else>
            <BaseButton
              v-if="!notification.read"
              kind="ghost"
              size="sm"
              density="comfortable"
              class="px-2"
              @click.stop="markAsRead"
            >
              {{ t('notifications.common.markAsRead') }}
            </BaseButton>
            <BaseButton kind="ghost" size="sm" density="comfortable" class="px-2 ms-2" @click.stop="deleteNotification">
              {{ t('notifications.common.delete') }}
            </BaseButton>
          </template>
        </div>
      </div>
    </div>
  </v-list-item>

  <v-list-item
    v-else
    :active="!notification.read"
    :class="{ 'unread-notification': !notification.read }"
    class="notification-item py-2 px-4 transition-fast-in-fast-out"
  >
    <div class="d-flex align-start w-100">
      <notification-avatar :notification="notification" class="me-3 mt-1" />

      <div class="flex-grow-1 d-flex flex-column">
        <div class="d-flex flex-row justify-space-between align-center notification-item__top">
          <component
            :is="contentComponent"
            ref="contentRef"
            :notification="notification"
            @update-notification="onUpdateNotification"
          />
          <span class="notification-item__time text-caption text-medium-emphasis">{{ formattedTime }}</span>
        </div>

        <!-- Generic row actions (mark read / delete) stay out of the way until the
             pointer hovers the row or keyboard focus lands inside it; touch devices
             have no hover, so they are always shown there. Type-specific actions
             (accept / decline) do not carry this class and stay visible. -->
        <div
          class="d-flex justify-end align-center mt-2"
          :class="{ 'notification-item__actions': !(renderedActions && renderedActions.length > 0) }"
        >
          <template v-if="renderedActions && renderedActions.length > 0">
            <BaseButton
              v-for="(action, index) in renderedActions"
              :key="index"
              :kind="action.color === 'error' ? 'danger' : 'ghost'"
              size="sm"
              density="comfortable"
              class="px-2 ms-2"
              @click.stop="action.handler"
            >
              {{ action.text }}
            </BaseButton>
          </template>
          <template v-else>
            <BaseButton
              v-if="!notification.read"
              kind="ghost"
              size="sm"
              density="comfortable"
              class="px-2"
              @click.stop="markAsRead"
            >
              {{ t('notifications.common.markAsRead') }}
            </BaseButton>
            <BaseButton kind="ghost" size="sm" density="comfortable" class="px-2 ms-2" @click.stop="deleteNotification">
              {{ t('notifications.common.delete') }}
            </BaseButton>
          </template>
        </div>
      </div>
    </div>
  </v-list-item>
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import type { Notification } from '@/network/api/notifications/types'
import type { RenderedNotificationContent } from './renders/NotificationRenderUtils'

import { computed, markRaw, onMounted, onUpdated, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useFormattedTime } from '@/utils/dateTime'

import NotificationAvatar from './NotificationAvatar.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { getNotificationRenderer } from '@/services/notification/registry'

const props = defineProps<{
  notification: Notification
  onMarkAsRead: (notificationId: number) => void
  onDelete: (notificationId: number) => void
}>()

const { t } = useI18n()
const { formatTime } = useFormattedTime()

const formattedTime = computed(() => formatTime(props.notification.createdAt))

const contentComponent = computed<Component>(() => {
  return getNotificationRenderer(props.notification.type)
})

const contentRef = ref<{ content: RenderedNotificationContent } | null>(null)

const contentCache = shallowRef<RenderedNotificationContent | undefined>(undefined)
const updateContentCache = () => {
  if (contentRef.value?.content) {
    contentCache.value = markRaw({ ...contentRef.value.content })
  }
}

const renderedActions = computed(() => contentCache.value?.actions || [])

const hasRouterLink = computed(() => {
  return !!contentCache.value?.routerLink
})

const routerLinkTarget = computed(() => {
  const link = contentCache.value?.routerLink
  if (!link) return { name: 'home' }

  return {
    name: String(link.name),
    params: link.params ? { ...link.params } : {},
    query: link.query ? { ...link.query } : {},
  }
})

let updateQueued = false
const queueContentUpdate = () => {
  if (!updateQueued) {
    updateQueued = true
    setTimeout(() => {
      updateContentCache()
      updateQueued = false
    }, 0)
  }
}

const checkContentUpdate = () => {
  if (contentRef.value?.content) {
    queueContentUpdate()
  }
}

onMounted(checkContentUpdate)
onUpdated(checkContentUpdate)

const markAsRead = (event: Event) => {
  event.stopPropagation()
  props.onMarkAsRead(props.notification.id)
}

const deleteNotification = (event: Event) => {
  event.stopPropagation()
  props.onDelete(props.notification.id)
}

const router = useRouter()

const navigateToTarget = () => {
  if (hasRouterLink.value) {
    props.onMarkAsRead(props.notification.id)
    router.push(routerLinkTarget.value).catch((error) => {
      if (error.name !== 'NavigationDuplicated') {
        console.error('导航错误:', error)
        toast.error(t('notifications.common.unreachable'))
      }
    })
  }
}

const onUpdateNotification = (notificationId: number) => {
  props.onMarkAsRead(notificationId)
}
</script>

<style scoped>
/* 未读靠 wash 底色区分，不靠左竖条。原来的写法还有一个更实际的问题：hover 和
   未读用的是同一个 4% 主色底，两者唯一的区别就是那条竖条——把条纹删掉而不换
   底色，未读态会直接消失。所以 hover 收回中性的 --fill，未读改用 --accent-wash。 */
.notification-item {
  transition: background-color 0.2s ease;
}

.notification-item:hover {
  background-color: var(--fill);
}

.unread-notification {
  background-color: var(--accent-wash);
}

/* 通用的「标记为已读 / 删除」是辅助动作：不占常驻的一行，悬停或键盘焦点落进来
   时才现身。只在真有指针的设备上这么做——触屏没有 hover，藏了就再也点不到。 */
@media (hover: hover) {
  .notification-item__actions {
    opacity: 0;
    transition: opacity var(--dur-quick) var(--ease-standard);
  }

  .notification-item:hover .notification-item__actions,
  .notification-item:focus-within .notification-item__actions {
    opacity: 1;
  }
}

/* 时间戳不许被挤：内容那一列可以收窄换行，它按原样待着。不这么写时，窄屏上
   「1 小时前」会被正文压成一列一个字，竖着排下来。 */
.notification-item__time {
  flex: none;
  margin-inline-start: 8px;
  white-space: nowrap;
}

/* 窄屏上正文占满一行，时间挪到它下面一行右对齐；上面那条留出的 8px 换成行距。 */
@media (max-width: 599.98px) {
  .notification-item__top {
    flex-wrap: wrap;
  }

  .notification-item__time {
    flex-basis: 100%;
    margin-inline-start: 0;
    text-align: right;
  }
}
</style>
