<template>
  <div class="notification-content">
    <span class="n-title">{{ title }}</span>
    <div v-if="body" class="n-sub">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getStringMetadata } from './NotificationRenderUtils'

import { renderNoticeMessage } from '@/lib/noticeText'

/**
 * 额度快用完了（`usage.alerts`）：团队方案、项目预算、成员任务上限三层之一
 * 用到八成。收件人是会被停下的人——团队额度到团队全体、项目预算到项目里
 * 的人、成员上限到本人。这一条只是提醒，调用照常放行。
 *
 * 两行：第一行说这是哪一层，和额度页顶部那一条的标题同一句话；第二行是平台算好的
 * 那一句数字（`payload.content` 带键，按读者的语言渲染）。
 *
 * 点开去能处理的地方：团队额度去那个团队的额度页（`teamHandle`），项目预算和成员
 * 上限去项目的设置页（`projectId`）。后端没给地址时退回收件箱，别落在一页不相干的
 * 地方。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const text = (key: string) => getStringMetadata(props.notification, key)

// 认不出层时用团队那一句：三层里它最重，宁可把话说重一层，也不要把「归你的任务会停」
// 说成「团队全体会停」。
const HEADINGS: Record<string, string> = {
  team: 'usage.warning.teamTitle',
  project: 'usage.warning.projectTitle',
  member: 'usage.warning.memberTitle',
}

const title = computed(() => t(HEADINGS[text('layer')] ?? 'usage.warning.teamTitle'))

const body = computed(
  () => renderNoticeMessage(props.notification.contextMetadata.message, text('content')) || text('content')
)

const routerLink = computed<RenderedNotificationContent['routerLink']>(() => {
  const handle = text('teamHandle')
  if (handle) return { name: 'TeamsDetailCredits', params: { handle } }
  const projectId = text('projectId')
  if (projectId) return { name: 'project-settings', params: { projectId, section: 'environment' } }
  return { name: 'inbox' }
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
