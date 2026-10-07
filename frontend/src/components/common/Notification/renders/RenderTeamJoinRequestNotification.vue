<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">
      <i18n-t v-if="requester" keypath="notifications.TEAM_JOIN_REQUEST.title" tag="span">
        <template #requester><UserRef :handle="requester.handle" :name="requester.name" :project-id="null" /></template>
      </i18n-t>
      <template v-else>{{ title }}</template>
    </div>
    <div class="text-body-2 text-medium-emphasis mt-1">{{ body }}</div>
    <div v-if="message" class="text-body-2 text-medium-emphasis mt-2 message-box">
      <v-icon icon="mdi-format-quote-open" size="12" class="me-1 text-primary-lighten-1" />
      {{ message }}
      <v-icon icon="mdi-format-quote-close" size="12" class="ms-1 text-primary-lighten-1" />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { useTeamJoinRequestAnswer } from '@/composables/useTeamJoinRequestAnswer'

import { getEntity, getStringMetadata, teamHandle } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<NotificationRenderProps>()
const emit = defineEmits<{
  (e: 'update-notification', notificationId: number): void
}>()
const { t } = useI18n()

// 答过的申请记在这份 composable 的模块作用域里，不记在这个实例里——见那里的注释。
const { answeredHere, answer: answerJoinRequest } = useTeamJoinRequestAnswer()

const requester = computed(() => getEntity(props.notification, 'requester'))
const team = computed(() => getEntity(props.notification, 'team'))
const message = computed(() => getStringMetadata(props.notification, 'message', ''))
// 这条申请本身：后端把它解析成 entities.application，状态是此刻的，不是发通知那一刻的。
const application = computed(() => getEntity(props.notification, 'application'))
const waiting = computed(
  () => !!team.value && application.value?.status === 'PENDING' && !answeredHere.has(application.value.id)
)

const title = computed(() => {
  if (!requester.value) return t('notifications.TEAM_JOIN_REQUEST.title_anonymous')
  return t('notifications.TEAM_JOIN_REQUEST.title', { requester: requester.value.name })
})

const body = computed(() => {
  return t('notifications.TEAM_JOIN_REQUEST.body', {
    team: team.value?.name || t('notifications.common.unknownTeam'),
  })
})

// 还在等审批的时候整行不跳走，用下面的两颗按钮答；答过了点进去看这个团队的申请。
const routerLink = computed(() => {
  if (waiting.value || !team.value) return undefined
  return {
    name: 'TeamsDetailMembers',
    params: { handle: teamHandle(team.value) },
    query: {
      tab: 'requests',
      applicationId: application.value?.id,
      type: 'request',
    },
  }
})

async function answer(approve: boolean) {
  const answered = await answerJoinRequest(application.value!.id, Number(team.value!.id), approve)
  // 答成了才告诉宿主：列表要重拉，这一行也该跟着换成答过的那一种。
  if (answered) emit('update-notification', props.notification.id)
}

const actions = computed(() =>
  waiting.value
    ? [
        { text: t('notifications.TEAM_JOIN_REQUEST.action.approve'), color: 'success', handler: () => answer(true) },
        { text: t('notifications.TEAM_JOIN_REQUEST.action.reject'), color: 'error', handler: () => answer(false) },
      ]
    : []
)

const content = computed<RenderedNotificationContent>(() => ({
  title: title.value,
  body: body.value,
  routerLink: routerLink.value,
  actions: actions.value,
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

.message-box {
  background-color: rgba(var(--v-theme-surface-variant), 0.15);
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 8px;
  font-style: italic;
  position: relative;
}
</style>
