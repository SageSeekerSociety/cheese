<template>
  <div class="notification-content">
    <span class="n-title">{{ title }}</span>
    <div v-if="body" class="n-sub">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'

import { getStringMetadata } from './NotificationRenderUtils'

import { renderNoticeMessage } from '@/lib/noticeText'

/**
 * 个人芝士自己做的事（后端 `routine.personal`）：一条例行事务运行完了、有操作待你确认、
 * 例行事务暂停了，或者芝士发给你的一句话。标题是那一句（平台的句子按读者的语言渲染，芝士
 * 的话原样），底下是芝士说的结果；点开在待办页打开那段对话。
 */
const props = defineProps<NotificationRenderProps>()

const text = (key: string) => getStringMetadata(props.notification, key)

const title = computed(
  () => renderNoticeMessage(props.notification.contextMetadata.message, text('content')) || text('content')
)
const body = computed(() => text('quote'))

const routerLink = computed(() => {
  const conversation = text('assistantConversation')
  return conversation ? { name: 'inbox', query: { assistant: conversation } } : undefined
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
