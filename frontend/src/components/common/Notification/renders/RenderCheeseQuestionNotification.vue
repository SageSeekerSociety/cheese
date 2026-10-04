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

import { getRoomTitle, getStringMetadata } from './NotificationRenderUtils'

/**
 * 芝士提出的待回答问题：本轮停在这里等回答。
 *
 * 标题就是问题原文，这里不改写、不套模板 —— 通知里读到的和回房间看到的是同一句。
 * 与 `ROOM_NOTICE` 分成两个组件，是因为那一条是平台的一行提示（定长、措辞由平台
 * 决定），这一条是芝士自己的话，长度取决于它怎么问。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const projectId = computed(() => getStringMetadata(props.notification, 'projectId'))
const topicId = computed(() => getStringMetadata(props.notification, 'topicId'))
const topicTitle = computed(() => getRoomTitle(props.notification))
// 这条提问在时间线上的那条消息 —— 投递的时候就一并存下了（`announce.notify_question`），
// 留着就是为了「点进去落在它身上」。
const blockId = computed(() => getStringMetadata(props.notification, 'blockId'))

const title = computed(
  () => getStringMetadata(props.notification, 'question') || t('notifications.CHEESE_QUESTION.untitled')
)

// 答过之后服务端把回答并进 `answered`（`ledger.settle`）：点的那一项，或者打字回的那句话。通知是一条事件记录，
// 会在收件箱里留到人删掉为止，所以它说的得是现在的状态：已经答过的题不能还说「待你回答」。
const answered = computed(() => getStringMetadata(props.notification, 'answered'))

const body = computed(() => {
  if (!topicTitle.value) return ''
  return answered.value
    ? t('notifications.CHEESE_QUESTION.answered', { topicTitle: topicTitle.value, answer: answered.value })
    : t('notifications.CHEESE_QUESTION.body', { topicTitle: topicTitle.value })
})

const routerLink = computed(() => {
  if (!projectId.value || !topicId.value) return undefined
  // 提问记的是「这一轮停在这儿」，可一条通知点开时，那条消息可能已经在几小时的时间
  // 线上游了很远 —— 「进房间」不等于「看到那一条」。房间页本来就认 `?block=`（搜索
  // 结果、别人发来的链接都走它），所以把提问那条消息的 id 一起带上，落下去就是落在
  // 它身上，而不是房间最新那几条。
  return {
    name: 'workspace-topic',
    params: { projectId: projectId.value, topicId: topicId.value },
    query: blockId.value ? { block: blockId.value } : undefined,
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
