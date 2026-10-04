<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">
      <i18n-t keypath="notifications.DEVICE_IN_USE.title" tag="span">
        <template #agent>
          <UserRef :handle="text('agentHandle')" :name="agentName" :project-id="text('projectId') || null" />
        </template>
        <template #device>{{ text('deviceName') }}</template>
      </i18n-t>
    </div>
    <div class="text-body-2 text-medium-emphasis mt-1">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getMetadata, getRoomTitle, getStringMetadata } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'
import { teammateName } from '@/lib/agentNames'

/**
 * 一个 agent 开始在你登记的设备上工作（#1900 第 5 步）。只是告知：哪个项目、哪个
 * 房间、哪个 agent，能不能访问整台机器。点进去是团队页，那里列着谁在用你的每台机器。
 */
const props = defineProps<NotificationRenderProps>()
const { t } = useI18n()

const text = (key: string) => getStringMetadata(props.notification, key)
const agentName = computed(() => teammateName(text('agentName'), text('agentNameSource')))

const title = computed(() =>
  t('notifications.DEVICE_IN_USE.title', { agent: agentName.value, device: text('deviceName') })
)

const body = computed(() => {
  const where = t('notifications.DEVICE_IN_USE.body', {
    project: text('projectName'),
    room: getRoomTitle(props.notification),
  })
  return getMetadata(props.notification, 'machineAccess', false)
    ? `${where} · ${t('notifications.DEVICE_IN_USE.wholeMachine')}`
    : where
})

const routerLink = computed(() => {
  const handle = text('teamHandle')
  return handle ? { name: 'TeamsDetailCompute', params: { handle } } : undefined
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
