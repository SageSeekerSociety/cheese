<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">{{ title }}</div>
    <div v-if="body" class="text-body-2 text-medium-emphasis mt-1">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getStringMetadata } from './NotificationRenderUtils'

/**
 * 平台在房间里说的、要人动手的那一句。
 *
 * 标题就是房间里那一行原文，这里不改写、不套模板：通知和房间对同一件事只有一种
 * 说法，点进去看到的和通知里读到的是同一句。副标题只回答「在哪个房间」。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const projectId = computed(() => getStringMetadata(props.notification, 'projectId'))
const topicId = computed(() => getStringMetadata(props.notification, 'topicId'))
const topicTitle = computed(() => getStringMetadata(props.notification, 'topicTitle'))

const title = computed(
  () => getStringMetadata(props.notification, 'content') || t('notifications.ROOM_NOTICE.untitled')
)

const body = computed(() =>
  topicTitle.value ? t('notifications.ROOM_NOTICE.body', { topicTitle: topicTitle.value }) : ''
)

const routerLink = computed(() => {
  if (!projectId.value || !topicId.value) return undefined
  return {
    name: 'workspace-topic',
    params: { projectId: projectId.value, topicId: topicId.value },
  }
})

const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
  routerLink: routerLink.value,
}))

defineExpose({
  content,
})
</script>

<style scoped>
.notification-content {
  display: flex;
  flex-direction: column;
}
</style>
