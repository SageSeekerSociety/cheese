<template>
  <div class="notification-content">
    <div class="text-subtitle-2 font-weight-medium">
      <i18n-t v-if="inviter" keypath="notifications.PROJECT_INVITE.title" tag="span">
        <template #inviter><UserRef :handle="inviter.handle" :name="inviter.name" :project-id="null" /></template>
      </i18n-t>
      <template v-else>{{ title }}</template>
    </div>
    <div class="text-body-2 text-medium-emphasis mt-1">{{ body }}</div>
  </div>
</template>

<script setup lang="ts">
import type { NotificationRenderProps, RenderedNotificationContent } from './NotificationRenderUtils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { useProjectInvitationAnswer } from '@/composables/useProjectInvitationAnswer'

import { getEntity, getStringMetadata } from './NotificationRenderUtils'

import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<NotificationRenderProps>()
const emit = defineEmits<{
  (e: 'update-notification', notificationId: number): void
}>()
const { t } = useI18n()

const inviter = computed(() => getEntity(props.notification, 'inviter'))
const project = computed(() => getEntity(props.notification, 'project'))
const projectName = computed(() => getStringMetadata(props.notification, 'projectName', project.value?.name || ''))
const invitationId = computed(() => getStringMetadata(props.notification, 'invitationId', ''))

// How the invitation ended. The server writes it once the invitation is answered or
// withdrawn; an answer given from this row is known here before the list reloads.
const { answered: answeredHere, answer: answerInvitation } = useProjectInvitationAnswer()
const settled = computed(() => getStringMetadata(props.notification, 'status', ''))
const status = computed(() => answeredHere.value ?? settled.value)
const isOpen = computed(() => !status.value)

const title = computed(() => {
  if (!inviter.value) return t('notifications.PROJECT_INVITE.title_anonymous')
  return t('notifications.PROJECT_INVITE.title', { inviter: inviter.value.name })
})

const body = computed(() => {
  const name = projectName.value || t('notifications.common.unknownProject')
  if (status.value === 'accepted' || status.value === 'declined' || status.value === 'revoked') {
    return t(`notifications.PROJECT_INVITE.ended.${status.value}`, { projectName: name })
  }
  return t('notifications.PROJECT_INVITE.body', { projectName: name })
})

// Accepted: the project itself. Still open on the server: the page that lists open
// invitations. An answer given here never takes the link away — `NotificationItem`
// renders a row with a link and one without as two instances, so dropping it would
// remount this one and lose the answer it is showing.
const routerLink = computed(() => {
  if (status.value === 'accepted' && project.value) {
    return { name: 'workspace-project', params: { projectId: project.value.id } }
  }
  if (!settled.value) return { name: 'HomeTeamsPending' }
  return undefined
})

async function answer(accept: boolean) {
  if (await answerInvitation(invitationId.value, accept)) emit('update-notification', props.notification.id)
}

const actions = computed(() => {
  if (!invitationId.value || !isOpen.value) return []
  return [
    { text: t('notifications.PROJECT_INVITE.action.accept'), color: 'success', handler: () => answer(true) },
    { text: t('notifications.PROJECT_INVITE.action.decline'), color: 'error', handler: () => answer(false) },
  ]
})

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
</style>
