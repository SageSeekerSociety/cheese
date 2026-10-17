<template>
  <div class="notification-content">
    <div class="n-title">
      <i18n-t v-if="reactor" scope="global" keypath="notifications.REACTION.title" tag="span">
        <template #reactor><UserRef :handle="reactor.handle" :name="reactor.name" :project-id="null" /></template>
      </i18n-t>
      <template v-else>{{ title }}</template>
      <span v-if="reactionEmoji" class="n-emoji">{{ reactionEmoji }}</span>
    </div>
    <div class="n-sub">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getEntity, getStringMetadata } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

// 获取实体和元数据
const reactor = computed(() => getEntity(props.notification, 'reactor'))
const reactionEmoji = computed(() => getStringMetadata(props.notification, 'reactionEmoji', '👍'))
const discussionTitle = computed(() => getStringMetadata(props.notification, 'discussionTitle', ''))

// 通知标题
const title = computed(() => {
  if (!reactor.value) return t('notifications.REACTION.title_anonymous')
  return t('notifications.REACTION.title', { reactor: reactor.value.name })
})

// 通知内容
const body = computed(() => {
  return t('notifications.REACTION.body', {
    reactionEmoji: reactionEmoji.value,
  })
})

// 导出渲染结果，供父组件使用
const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
}))

defineExpose({
  content,
})
</script>
