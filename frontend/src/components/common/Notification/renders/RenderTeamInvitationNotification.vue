<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">
      <i18n-t v-if="inviter" scope="global" keypath="notifications.TEAM_INVITATION.title" tag="span">
        <template #inviter><UserRef :handle="inviter.handle" :name="inviter.name" :project-id="null" /></template
        ><template #team>{{ team?.name || '' }}</template>
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

import { getEntity, getStringMetadata, teamHandle } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<NotificationRenderProps>()
const emit = defineEmits<{
  (e: 'update-notification', notificationId: number): void
}>()
const { t } = useI18n()

// 答过的邀请记在宿主那份桥的模块作用域里，不记在这个实例里——见 `useNotificationAnswers` 的注释。

// 获取实体和元数据
const inviter = computed(() => getEntity(props.notification, 'inviter'))
const team = computed(() => getEntity(props.notification, 'team'))
// 后端给的是角色码（OWNER / ADMIN / MEMBER），写给人看的是团队成员页上的叫法；
// 这一版不认识的码说成「成员」，不把码原样印出来。
const ROLE_LABELS: Record<string, string> = {
  OWNER: 'teams.members.roleOwner',
  ADMIN: 'teams.members.roleAdmin',
  MEMBER: 'teams.members.roleMember',
}
const role = computed(() => {
  const key = ROLE_LABELS[getStringMetadata(props.notification, 'role', '')]
  return key ? t(key) : t('notifications.common.member')
})
const message = computed(() => getStringMetadata(props.notification, 'message', ''))
// 这条邀请本身：后端把它解析成 entities.application，状态是此刻的，不是发通知那一刻的。
const application = computed(() => getEntity(props.notification, 'application'))
// 宿主没接上答邀请那份桥（`props.answering` 不在）就没有那几颗按钮，只剩一条去处的链接。
const waiting = computed(
  () =>
    !!props.answering &&
    application.value?.status === 'PENDING' &&
    !props.answering.teamInvitationAnswered(application.value.id)
)

// 通知标题
const title = computed(() => {
  if (!inviter.value) return t('notifications.TEAM_INVITATION.title_anonymous', { team: team.value?.name || '' })
  return t('notifications.TEAM_INVITATION.title', { inviter: inviter.value.name, team: team.value?.name || '' })
})

// 通知内容
const body = computed(() => {
  return t('notifications.TEAM_INVITATION.body', {
    role: role.value,
    team: team.value?.name || t('notifications.common.unknownTeam'),
  })
})

// 还在等回答的时候整行不跳走，用下面的两颗按钮答；答过了点进去看这个团队。
const routerLink = computed(() => {
  if (waiting.value || !team.value) return undefined
  return {
    name: 'TeamsDetailMembers',
    params: { handle: teamHandle(team.value) },
    query: {
      tab: 'invitations',
      applicationId: application.value?.id,
      type: 'invitation',
    },
  }
})

async function answer(accept: boolean) {
  const applicationId = application.value?.id
  if (!props.answering || !applicationId) return
  const answered = await props.answering.answerTeamInvitation(applicationId, accept)
  // 答成了才告诉宿主：列表要重拉，这一行也该跟着换成答过的那一种。
  if (answered) emit('update-notification', props.notification.id)
}

const actions = computed(() =>
  waiting.value
    ? [
        { text: t('notifications.TEAM_INVITATION.action.accept'), color: 'success', handler: () => answer(true) },
        { text: t('notifications.TEAM_INVITATION.action.decline'), color: 'error', handler: () => answer(false) },
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
