<template>
  <div class="notification-content">
    <div class="n-title">{{ title }}</div>
    <div class="n-sub">
      <i18n-t v-if="rejector" scope="global" keypath="notifications.TEAM_REQUEST_REJECTED.body" tag="span">
        <template #rejector><UserRef :handle="rejector.handle" :name="rejector.name" :project-id="null" /></template>
        <template #team>{{ team?.name || t('notifications.common.unknownTeam') }}</template>
      </i18n-t>
      <template v-else>{{ body }}</template>
    </div>
    <div v-if="rejectReason" class="n-quote">
      <span class="n-label">{{ t('notifications.common.reason') }}</span
      >{{ rejectReason }}
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
const rejector = computed(() => getEntity(props.notification, 'rejector'))
const team = computed(() => getEntity(props.notification, 'team'))
const rejectReason = computed(() => getStringMetadata(props.notification, 'reason', ''))
const applicationId = computed(() => getStringMetadata(props.notification, 'applicationId', ''))

// 通知标题
const title = computed(() => {
  return t('notifications.TEAM_REQUEST_REJECTED.title')
})

// 通知内容
const body = computed(() => {
  return t('notifications.TEAM_REQUEST_REJECTED.body', {
    rejector: rejector.value?.name || t('notifications.common.teamAdmin'),
    team: team.value?.name || t('notifications.common.unknownTeam'),
  })
})

// 构建路由链接 - 拒绝后通常不提供链接，或者可以提供查找其他团队的链接
const routerLink = computed(() => {
  return {
    name: 'HomeTeamsPending',
    params: {},
    query: { search: team.value?.name || '' },
  }
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
