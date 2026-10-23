<template>
  <div class="notification-content">
    <div class="n-title">
      <i18n-t v-if="decliner" scope="global" keypath="notifications.TEAM_INVITATION_DECLINED.title" tag="span">
        <template #decliner><UserRef :handle="decliner.handle" :name="decliner.name" :project-id="null" /></template>
      </i18n-t>
      <template v-else>{{ title }}</template>
    </div>
    <div class="n-sub">{{ body }}</div>
    <div v-if="declineReason" class="n-quote">
      <span class="n-label">{{ t('notifications.common.reason') }}</span
      >{{ declineReason }}
    </div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getEntity, getStringMetadata, teamHandle } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

// 获取实体和元数据
const decliner = computed(() => getEntity(props.notification, 'decliner'))
const team = computed(() => getEntity(props.notification, 'team'))
const declineReason = computed(() => getStringMetadata(props.notification, 'reason', ''))
const applicationId = computed(() => getStringMetadata(props.notification, 'applicationId', ''))

// 通知标题
const title = computed(() => {
  if (!decliner.value) return t('notifications.TEAM_INVITATION_DECLINED.title_anonymous')
  return t('notifications.TEAM_INVITATION_DECLINED.title', { decliner: decliner.value.name })
})

// 通知内容
const body = computed(() => {
  return t('notifications.TEAM_INVITATION_DECLINED.body', {
    team: team.value?.name || t('notifications.common.unknownTeam'),
  })
})

// 构建路由链接
const routerLink = computed(() => {
  if (team.value) {
    return {
      name: 'TeamsDetailMembers',
      params: { handle: teamHandle(team.value) },
      query: { tab: 'invitations' },
    }
  }
  return undefined
})

// 导出渲染结果，供父组件使用
const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
  routerLink: routerLink.value,
}))

defineExpose({
  content,
})
</script>
