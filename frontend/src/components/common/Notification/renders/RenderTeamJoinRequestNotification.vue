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

<script lang="ts">
import { reactive } from 'vue'

// 在这儿答过的申请。记在组件之外：答完这一行会换成带链接的那一种，渲染器跟着重建，
// 而列表要到下次重拉才拿到新状态；记在实例里的话，重建之后按钮又回来了，再点只会
// 报「找不到」。
const answeredHere = reactive(new Set<string>())
</script>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { getEntity, getStringMetadata, teamHandle } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'
import { TeamsApi } from '@/network/api/teams'

const props = defineProps<NotificationRenderProps>()
const emit = defineEmits<{
  (e: 'update-notification', notificationId: number): void
}>()
const { t } = useI18n()

const requester = computed(() => getEntity(props.notification, 'requester'))
const team = computed(() => getEntity(props.notification, 'team'))
const message = computed(() => getStringMetadata(props.notification, 'message', ''))
// 这条申请本身：后端把它解析成 entities.application，状态是此刻的，不是发通知那一刻的。
const application = computed(() => getEntity(props.notification, 'application'))
const waiting = computed(
  () => !!team.value && application.value?.status === 'PENDING' && !answeredHere.has(application.value.id)
)
// 请求还没回来时再点不算数：连点或者点完批准又点拒绝，只发第一下。
const sending = ref(false)

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
  if (sending.value) return
  sending.value = true
  const id = application.value!.id
  const teamId = Number(team.value!.id)
  try {
    await (approve ? TeamsApi.approveJoinRequest(teamId, Number(id)) : TeamsApi.rejectJoinRequest(teamId, Number(id)))
    answeredHere.add(id)
    toast.success(
      t(approve ? 'notifications.TEAM_JOIN_REQUEST.toast.approved' : 'notifications.TEAM_JOIN_REQUEST.toast.rejected')
    )
    emit('update-notification', props.notification.id)
  } catch (error) {
    console.error('Failed to answer team join request', error)
    toast.error(
      t(
        approve
          ? 'notifications.TEAM_JOIN_REQUEST.toast.approveFailed'
          : 'notifications.TEAM_JOIN_REQUEST.toast.rejectFailed'
      )
    )
  } finally {
    sending.value = false
  }
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
