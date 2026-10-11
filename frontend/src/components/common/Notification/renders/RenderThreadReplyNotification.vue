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

import { getRoomTitle, getStringMetadata, messageLink } from './NotificationRenderUtils'

/**
 * 我参与过的支线里有人回复了。标题说谁、在哪个频道，正文是那条回复的开头；点开落在
 * 支线里的那条回复上（后端 `agent.mentions._tell_thread`）。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const title = computed(() =>
  t('notifications.THREAD_REPLY.title', {
    who: getStringMetadata(props.notification, 'authorName') || getStringMetadata(props.notification, 'author'),
    topicTitle: getRoomTitle(props.notification),
  })
)
const body = computed(() => getStringMetadata(props.notification, 'content'))

const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
  routerLink: messageLink(props.notification),
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
