<template>
  <PendingView
    :my-requests="myRequests"
    :loading-my-requests="loadingMyRequests"
    :failed-my-requests="failedMyRequests"
    :my-requests-error="myRequestsError"
    :my-invitations="myInvitations"
    :loading-my-invitations="loadingMyInvitations"
    :failed-my-invitations="failedMyInvitations"
    :my-invitations-error="myInvitationsError"
    :project-invitations="projectInvitations"
    :loading-project-invitations="loadingProjectInvitations"
    :failed-project-invitations="failedProjectInvitations"
    :project-invitations-error="projectInvitationsError"
    :answering="answering"
    :resolve-user="resolveUser"
    @retry-requests="fetchMyJoinRequests"
    @retry-invitations="fetchMyInvitations"
    @retry-project-invitations="fetchProjectInvitations"
    @cancel-request="cancelRequest"
    @accept-invitation="acceptInvitation"
    @decline-invitation="declineInvitation"
    @answer-project-invitation="answerProjectInvitation"
    @navigate="navigate"
  />
</template>

<script setup lang="ts">
// 「待办 / 申请与邀请」这一页的**容器**：三段取数、取消/接受/拒绝、人名与去处都在
// 这儿；画的那一半在 `PendingView.vue`。
import type { ProjectInvitation } from '@/cx_types'
import type { TeamMembershipApplication } from '@/types'

import { onMounted, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { useUserRefResolver } from '@/composables/useUserRefResolver'

import PendingView from './PendingView.vue'

import { listMyInvitations, respondToInvitation } from '@/api'
import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'

const { resolve: resolveUser, navigate } = useUserRefResolver()

// 申请和邀请的状态
const myRequests = ref<TeamMembershipApplication[]>([])
const myInvitations = ref<TeamMembershipApplication[]>([])
const loadingMyRequests = ref(false)
const loadingMyInvitations = ref(false)
// 读失败和「一条都没有」是两件事：失败留在各自那一段里，空状态才说「暂无」。
const failedMyRequests = ref(false)
const myRequestsError = ref<string | null>(null)
const failedMyInvitations = ref(false)
const myInvitationsError = ref<string | null>(null)

// 项目邀请（cheesex 那一半）。和上面的小队邀请是两回事，只是同一种「等你答复」。
const projectInvitations = ref<ProjectInvitation[]>([])
const loadingProjectInvitations = ref(false)
const failedProjectInvitations = ref(false)
const projectInvitationsError = ref<string | null>(null)
const answering = ref<string | null>(null)

const fetchProjectInvitations = async () => {
  loadingProjectInvitations.value = true
  failedProjectInvitations.value = false
  projectInvitationsError.value = null
  try {
    projectInvitations.value = (await listMyInvitations()).data
  } catch (error) {
    // 这一段拿不到就在它自己那一格说清楚：小队那两段是这一页的主体，不该被它拖垮。
    failedProjectInvitations.value = true
    projectInvitationsError.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loadingProjectInvitations.value = false
  }
}

const answerProjectInvitation = async (invitation: ProjectInvitation, accept: boolean) => {
  answering.value = invitation.id
  try {
    await respondToInvitation(invitation.id, accept)
    toast.success(accept ? t('teams.pending.joinedProject') : t('teams.pending.declineDone'))
    await fetchProjectInvitations()
  } catch (error) {
    console.error('Failed to answer the project invitation', error)
    toast.error(t('teams.pending.answerFailed'))
  } finally {
    answering.value = null
  }
}

// 获取我发起的申请
const fetchMyJoinRequests = async () => {
  loadingMyRequests.value = true
  failedMyRequests.value = false
  myRequestsError.value = null
  try {
    const response = await TeamsApi.listMyJoinRequests()
    myRequests.value = response.data.requests
  } catch (error) {
    console.error('Failed to load join requests', error)
    failedMyRequests.value = true
    myRequestsError.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loadingMyRequests.value = false
  }
}

// 获取我收到的邀请
const fetchMyInvitations = async () => {
  loadingMyInvitations.value = true
  failedMyInvitations.value = false
  myInvitationsError.value = null
  try {
    const response = await TeamsApi.listMyInvitations()
    myInvitations.value = response.data.invitations
  } catch (error) {
    console.error('Failed to load invitations', error)
    failedMyInvitations.value = true
    myInvitationsError.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loadingMyInvitations.value = false
  }
}

// 取消申请
const cancelRequest = async (requestId: number) => {
  try {
    await TeamsApi.cancelMyJoinRequest(requestId)
    toast.success(t('teams.pending.cancelDone'))
    await fetchMyJoinRequests()
  } catch (error) {
    console.error('Failed to cancel the join request', error)
    toast.error(t('teams.pending.cancelFailed'))
  }
}

// 接受邀请
const acceptInvitation = async (invitationId: number) => {
  try {
    await TeamsApi.acceptInvitation(invitationId)
    toast.success(t('teams.pending.acceptDone'))
    await fetchMyInvitations()
  } catch (error) {
    console.error('Failed to accept the invitation', error)
    toast.error(t('teams.pending.acceptFailed'))
  }
}

// 拒绝邀请
const declineInvitation = async (invitationId: number) => {
  try {
    await TeamsApi.declineInvitation(invitationId)
    toast.success(t('teams.pending.declineDone'))
    await fetchMyInvitations()
  } catch (error) {
    console.error('Failed to decline the invitation', error)
    toast.error(t('teams.pending.declineFailed'))
  }
}

onMounted(async () => {
  await fetchMyJoinRequests()
  await fetchMyInvitations()
  await fetchProjectInvitations()
})
</script>
