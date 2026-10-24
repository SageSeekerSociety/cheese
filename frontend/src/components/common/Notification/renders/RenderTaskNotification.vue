<template>
  <div class="notification-content">
    <span class="n-title">{{ title }}</span>
    <div class="n-sub">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'

import { getStringMetadata } from './NotificationRenderUtils'

import { renderNoticeMessage } from '@/lib/noticeText'

/**
 * 空间里一道题目上的事（后端 `task_notices`）：领取通过或没通过、提交批改了、提交要求
 * 或个人截止时间改了、截止快到了、有人过了截止没交。标题是后端说的那一句（`message` 是
 * 它的键，按读者的语言渲染），底下是哪个空间；点开去题目页，批改结果去「我的提交」。
 */
const props = defineProps<NotificationRenderProps>()

const text = (key: string) => getStringMetadata(props.notification, key)

const title = computed(
  () => renderNoticeMessage(props.notification.contextMetadata.message, text('content')) || text('content')
)
const body = computed(() => text('spaceName'))

const routerLink = computed(() => {
  const spaceId = text('spaceId')
  const taskId = text('spaceTaskId')
  if (!spaceId || !taskId) return undefined
  const params = { spaceId, taskId }
  return text('event') === 'reviewed' ? { name: 'TasksSubmissions', params } : { name: 'TasksDetail', params }
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
