<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">
      <i18n-t v-if="mentioner && !inChannel" scope="global" keypath="notifications.MENTION.title" tag="span">
        <template #mentioner><UserRef :handle="mentioner.handle" :name="mentioner.name" :project-id="null" /></template>
      </i18n-t>
      <template v-else>{{ title }}</template>
    </div>
    <div v-if="body" class="text-body-2 text-medium-emphasis mt-1">{{ body }}</div>
    <div v-if="previewContent" class="text-body-2 text-medium-emphasis mt-2 preview-box">
      <v-icon icon="mdi-text-box-outline" size="14" class="me-1 text-medium-emphasis" />
      {{ previewContent }}
    </div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getEntity, getRoomTitle, getStringMetadata, messageLink } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

// 两种 @：讨论区里的（`mentioner` 是个用户实体），和频道里一条消息的（带着
// `topicId`，后端 `agent.mentions`）。后者标题说谁、在哪个频道，正文是那条消息的
// 开头，点开落在那条消息上。
const inChannel = computed(() => !!getStringMetadata(props.notification, 'topicId'))

// 获取实体和元数据
const mentioner = computed(() => getEntity(props.notification, 'mentioner'))
const discussionTitle = computed(() => getStringMetadata(props.notification, 'discussionTitle', ''))
const previewContent = computed(() => getStringMetadata(props.notification, 'previewContent', ''))

// 通知标题
const title = computed(() => {
  if (inChannel.value) {
    return t('notifications.MENTION.inChannel', {
      who: getStringMetadata(props.notification, 'authorName') || getStringMetadata(props.notification, 'author'),
      topicTitle: getRoomTitle(props.notification),
    })
  }
  if (!mentioner.value) return t('notifications.MENTION.title_anonymous')
  return t('notifications.MENTION.title', { mentioner: mentioner.value.name })
})

// 通知内容
const body = computed(() => {
  if (inChannel.value) return getStringMetadata(props.notification, 'content')
  return t('notifications.MENTION.body', {
    discussionTitle: discussionTitle.value || t('notifications.common.unknownDiscussion'),
  })
})

// 导出渲染结果，供父组件使用
const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
  routerLink: inChannel.value ? messageLink(props.notification) : undefined,
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

.preview-box {
  background-color: rgba(var(--v-theme-surface-variant), 0.5);
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 8px;
  position: relative;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
