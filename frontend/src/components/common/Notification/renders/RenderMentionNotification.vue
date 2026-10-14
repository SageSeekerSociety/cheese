<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">
      <i18n-t
        v-if="mentioner && !inChannel && !documentId"
        scope="global"
        keypath="notifications.MENTION.title"
        tag="span"
      >
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
// 第三种：文档里的 @（带着 `documentId`）：正文里的（后端 `api.doc_mentions`），或一条
// 评论里的（再带着 `commentId`）。说是哪份文档，
// 点开落在那份文档：任务的实况文档在任务里，项目自己的文档在文档页。
const documentId = computed(() => getStringMetadata(props.notification, 'documentId'))

// 获取实体和元数据
const mentioner = computed(() => getEntity(props.notification, 'mentioner'))
const discussionTitle = computed(() => getStringMetadata(props.notification, 'discussionTitle', ''))
const previewContent = computed(() => getStringMetadata(props.notification, 'previewContent', ''))

// 通知标题
const title = computed(() => {
  if (documentId.value) {
    // 评论里的 @ 带着 `commentId`（后端 `agent.mentions`），说「在评论里」。
    const key = getStringMetadata(props.notification, 'commentId') ? 'inDocumentComment' : 'inDocument'
    return t(`notifications.MENTION.${key}`, {
      who: getStringMetadata(props.notification, 'authorName') || getStringMetadata(props.notification, 'author'),
      document: getStringMetadata(props.notification, 'documentTitle') || t('notifications.common.untitledDocument'),
    })
  }
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
  if (documentId.value || inChannel.value) return getStringMetadata(props.notification, 'content')
  return t('notifications.MENTION.body', {
    discussionTitle: discussionTitle.value || t('notifications.common.unknownDiscussion'),
  })
})

/** 项目自己的一份文档（不在哪个频道里）：落到那份文档。 */
function documentLink(): RenderedNotificationContent['routerLink'] {
  const projectId = getStringMetadata(props.notification, 'projectId')
  if (!documentId.value || !projectId) return undefined
  return { name: 'project-document', params: { projectId, docId: documentId.value } }
}

// 导出渲染结果，供父组件使用
const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
  routerLink: inChannel.value ? messageLink(props.notification) : documentLink(),
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
