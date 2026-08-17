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
        <div class="d-flex flex-row justify-space-between align-center">
          <component
            :is="contentComponent"
            ref="contentRef"
            :notification="notification"
            @update-notification="onUpdateNotification"
          />
          <span class="text-caption text-medium-emphasis ms-2">{{ formattedTime }}</span>
        </div>

        <div class="d-flex justify-end align-center mt-2">
          <template v-if="renderedActions && renderedActions.length > 0">
            <v-btn
              v-for="(action, index) in renderedActions"
              :key="index"
              variant="text"
              density="comfortable"
              size="small"
              :color="action.color || 'primary'"
              class="px-2 ms-2"
              @click.stop="action.handler"
            >
              {{ action.text }}
            </v-btn>
          </template>
          <template v-else>
            <v-btn
              v-if="!notification.read"
              variant="text"
              density="comfortable"
              size="small"
              :color="notificationColor"
              class="px-2"
              @click.stop="markAsRead"
            >
              {{ t('notifications.common.markAsRead') }}
            </v-btn>
            <v-btn
              variant="text"
              density="comfortable"
              size="small"
              color="error"
              class="px-2 ms-2"
              @click.stop="deleteNotification"
            >
              {{ t('notifications.common.delete') }}
            </v-btn>
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
        <div class="d-flex flex-row justify-space-between align-center">
          <component
            :is="contentComponent"
            ref="contentRef"
            :notification="notification"
            @update-notification="onUpdateNotification"
          />
          <span class="text-caption text-medium-emphasis ms-2">{{ formattedTime }}</span>
        </div>

        <div class="d-flex justify-end align-center mt-2">
          <template v-if="renderedActions && renderedActions.length > 0">
            <v-btn
              v-for="(action, index) in renderedActions"
              :key="index"
              variant="text"
              density="comfortable"
              size="small"
              :color="action.color || 'primary'"
              class="px-2 ms-2"
              @click.stop="action.handler"
            >
              {{ action.text }}
            </v-btn>
          </template>
          <template v-else>
            <v-btn
              v-if="!notification.read"
              variant="text"
              density="comfortable"
              size="small"
              :color="notificationColor"
              class="px-2"
              @click.stop="markAsRead"
            >
              {{ t('notifications.common.markAsRead') }}
            </v-btn>
            <v-btn
              variant="text"
              density="comfortable"
              size="small"
              color="error"
              class="px-2 ms-2"
              @click.stop="deleteNotification"
            >
              {{ t('notifications.common.delete') }}
            </v-btn>
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

import { getNotificationColor, getNotificationRenderer } from '@/services/notification/registry'

const props = defineProps<{
  notification: Notification
  onMarkAsRead: (notificationId: number) => void
  onDelete: (notificationId: number) => void
}>()

const { t } = useI18n()
const { formatTime } = useFormattedTime()

const formattedTime = computed(() => formatTime(props.notification.createdAt))

const notificationColor = computed(() => getNotificationColor(props.notification.type))

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
        toast.error('无法访问该页面，您可能没有权限或页面不存在')
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
</style>
