<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">
      <i18n-t v-if="reactor" scope="global" keypath="notifications.REACTION.title" tag="span">
        <template #reactor><UserRef :handle="reactor.handle" :name="reactor.name" :project-id="null" /></template>
      </i18n-t>
      <template v-else>{{ title }}</template>
    </div>
    <div class="text-body-2 text-medium-emphasis mt-1">{{ body }}</div>
    <div v-if="reactionEmoji" class="text-body-2 text-medium-emphasis mt-2 emoji-box">
      <span class="reaction-emoji">{{ reactionEmoji }}</span>
    </div>
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

<style scoped>
.notification-content {
  display: flex;
  flex-direction: column;
}

.emoji-box {
  background-color: rgba(var(--v-theme-surface-variant), 0.5);
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.reaction-emoji {
  font-size: 24px;
  line-height: 1;
}
</style>
