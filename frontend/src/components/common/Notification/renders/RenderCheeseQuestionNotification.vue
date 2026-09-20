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
 * 芝士提出的待确认问题：本轮停在这里等回答。
 *
 * 标题就是问题原文，这里不改写、不套模板 —— 通知里读到的和回房间看到的是同一句。
 * 与 `ROOM_NOTICE` 分成两个组件，是因为那一条是平台的一行提示（定长、措辞由平台
 * 决定），这一条是芝士自己的话，长度取决于它怎么问。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const projectId = computed(() => getStringMetadata(props.notification, 'projectId'))
const topicId = computed(() => getStringMetadata(props.notification, 'topicId'))
const topicTitle = computed(() => getStringMetadata(props.notification, 'topicTitle'))

const title = computed(
  () => getStringMetadata(props.notification, 'question') || t('notifications.CHEESE_QUESTION.untitled')
)

const body = computed(() =>
  topicTitle.value ? t('notifications.CHEESE_QUESTION.body', { topicTitle: topicTitle.value }) : ''
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
