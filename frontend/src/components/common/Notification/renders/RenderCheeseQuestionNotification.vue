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
 * 芝士提出的待回答问题：本轮停在这里等回答。
 *
 * 标题就是问题原文，这里不改写、不套模板 —— 通知里读到的和回房间看到的是同一句。
 * 与 `ROOM_NOTICE` 分成两个组件，是因为那一条是平台的一行提示（定长、措辞由平台
 * 决定），这一条是芝士自己的话，长度取决于它怎么问。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const topicTitle = computed(() => getRoomTitle(props.notification))

const title = computed(
  () => getStringMetadata(props.notification, 'question') || t('notifications.CHEESE_QUESTION.untitled')
)

// 答过之后服务端把回答并进 `answered`（`ledger.settle`）：点的那一项，或者打字回的那句话。通知是一条事件记录，
// 会在收件箱里留到人删掉为止，所以它说的得是现在的状态：已经答过的题不能还说「待你回答」。
const answered = computed(() => getStringMetadata(props.notification, 'answered'))
// 被问的人跳过了这道题（不回答）时，服务端并进的是 `skipped`：同样不再等他。
const skipped = computed(() => !!getStringMetadata(props.notification, 'skipped'))

const body = computed(() => {
  if (!topicTitle.value) return ''
  if (answered.value)
    return t('notifications.CHEESE_QUESTION.answered', { topicTitle: topicTitle.value, answer: answered.value })
  if (skipped.value) return t('notifications.CHEESE_QUESTION.skipped', { topicTitle: topicTitle.value })
  return t('notifications.CHEESE_QUESTION.body', { topicTitle: topicTitle.value })
})

// 落点是问这道题的那条会话里的那条提问消息，而不是房间最新那几条（`messageLink`）。
const routerLink = computed(() => messageLink(props.notification))

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
