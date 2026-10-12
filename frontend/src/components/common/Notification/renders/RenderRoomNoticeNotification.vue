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

import { renderNoticeMessage } from '@/lib/noticeText'

/**
 * 平台在房间里说的、要人动手的那一句。
 *
 * 标题就是房间里那一行：`message` 是它的键和参数，按读者当前的语言渲染，和房间里
 * 那一行走同一份目录；没有键的旧通知显示存下的原文。通知和房间对同一件事只有一种
 * 说法。副标题一般只回答「在哪个房间」。
 *
 * 卡一有决定，服务端把结局并进 `outcome`（`ledger.settle_card`）：那一句不再等谁
 * 动手了，副标题改说结局，和芝士提问答完之后一样（`RenderCheeseQuestionNotification`）。
 * 标题留着房间当时那一行 —— 那是记录，不是现在的话。
 */
const props = defineProps<NotificationRenderProps>()
const { t, te } = useI18n()

const topicTitle = computed(() => getRoomTitle(props.notification))
const outcome = computed(() => getStringMetadata(props.notification, 'outcome'))

const title = computed(
  () =>
    renderNoticeMessage(props.notification.contextMetadata.message, getStringMetadata(props.notification, 'content')) ||
    t('notifications.ROOM_NOTICE.untitled')
)

// 结局词认不出来时说「办结」：`outcome` 是服务端的词，这一版前端还不认识它时，
// 也不能把英文原样端到人眼前。
const outcomeWord = computed(() =>
  te(`notifications.ROOM_NOTICE.outcome.${outcome.value}`)
    ? t(`notifications.ROOM_NOTICE.outcome.${outcome.value}`)
    : t('notifications.ROOM_NOTICE.settledUnknown')
)

const body = computed(() => {
  if (!topicTitle.value) return ''
  return outcome.value
    ? t('notifications.ROOM_NOTICE.settled', {
        topicTitle: topicTitle.value,
        outcome: outcomeWord.value,
      })
    : t('notifications.ROOM_NOTICE.body', { topicTitle: topicTitle.value })
})

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
