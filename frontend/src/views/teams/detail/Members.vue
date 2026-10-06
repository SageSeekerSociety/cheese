<template>
  <MembersView
    :can-bring-people-in="canBringPeopleIn"
    :active-tab="activeTab"
    :invite-dialog="isInviteDialogActive"
    :invite-query="inviteQuery"
    :invite-looking-up="lookingUp"
    :invite-lookup-error="lookupError"
    :invite-found="found"
    :invite-role="inviteRoleInput"
    :invite-message="inviteMessageInput"
    :members="teamMembers"
    :members-failed="failedMembers"
    :members-error="membersError"
    :requests="joinRequests"
    :requests-loading="loadingRequests"
    :requests-failed="failedRequests"
    :requests-error="requestsError"
    :answering="answering"
    :invitations="teamInvitations"
    :invitations-loading="loadingInvitations"
    :invitations-failed="failedInvitations"
    :invitations-error="invitationsError"
    :is-self-owner="isSelfOwner"
    :is-self-admin="isSelfAdmin"
    :resolve-user="resolveUser"
    @update:active-tab="activeTab = $event"
    @update:invite-dialog="isInviteDialogActive = $event"
    @update:invite-query="inviteQuery = $event"
    @update:invite-role="inviteRoleInput = $event"
    @update:invite-message="inviteMessageInput = $event"
    @retry-members="retryMembers"
    @retry-requests="retryRequests"
    @retry-invitations="retryInvitations"
    @submit-invite="confirmInvite"
    @promote="promoteToAdmin"
    @demote="demoteToMember"
    @remove="removeMember"
    @approve="approveRequest"
    @reject="rejectRequest"
    @cancel-invite="cancelInvitation"
    @navigate="navigate"
  >
    <template #joinLink>
      <TeamJoinLinkCard
        v-if="teamData && canBringPeopleIn"
        :team="teamData"
        :link="joinLink"
        :busy="joinLinkBusy"
        :error="joinLinkError"
        :copied="joinLinkCopied"
        :url="joinLinkUrl"
        :handle="joinHandle"
        :handle-error="joinHandleError"
        :address-prefix="joinAddressPrefix"
        @update:handle="joinHandle = $event"
        @save="saveJoinHandle"
        @reset="resetJoinLink"
        @set-approval="setJoinApproval"
        @set-visibility="setJoinVisibility"
        @copy="copyJoinLink"
      />
    </template>
  </MembersView>
</template>

<script setup lang="ts">
import type { TeamMember, TeamMembershipApplication } from '@/types'

import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useAccountLookup } from '@/composables/useAccountLookup'
import { useUserRefResolver } from '@/composables/useUserRefResolver'

import MembersView from './MembersView.vue'
import TeamJoinLinkCard from './TeamJoinLinkCard.vue'
import { useTeamJoinLinkCard } from './useTeamJoinLinkCard'

import { ApiError } from '@/api'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { TeamsApi } from '@/network/api/teams'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'

const route = useRoute()
const isInviteDialogActive = ref(false)
const teamMembers = ref<TeamMember[]>([])
// 读失败和「还没有成员」是两件事：失败替换掉这一格的内容，空状态才说「暂无」。
const failedMembers = ref(false)
const membersError = ref<string | null>(null)
const teamData = inject(teamDataInjectionKey, ref())
const dialog = useDialog()

const activeTab = ref('members')

const isSelfOwner = computed(() => {
  if (!teamData.value) {
    return false
  }
  return AccountService.user?.id === teamData.value.owner.id
})

// 看服务端给的 role，不看 admins.examples：那份名单最多只有 3 个人，第 4 个管理员会被当成普通成员。
const isSelfAdmin = computed(() => teamData.value?.role === 'OWNER' || teamData.value?.role === 'ADMIN')

// 邀请、加入链接、加入申请：把人带进团队的几样。移出成员不在其内：那是往外走，不是往里进。
const canBringPeopleIn = computed(() => isSelfAdmin.value && !teamData.value?.personal)

// 自己名下只有自己，没有成员这一页（侧栏里也不列）：直接输地址进来的，带回它的项目。
const router = useRouter()
watch(
  () => teamData.value?.personal,
  (own) => {
    if (own && teamData.value)
      void router.replace({ name: 'TeamsDetailDefault', params: { handle: teamData.value.handle } })
  },
  { immediate: true }
)

const { resolve: resolveUser, navigate } = useUserRefResolver()

// 小队链接卡片的取数收在 composable 里：它会改团队地址、重置链接、改审批与可见性，
// 改动回手就更新 teamData。
const {
  link: joinLink,
  busy: joinLinkBusy,
  error: joinLinkError,
  copied: joinLinkCopied,
  url: joinLinkUrl,
  addressPrefix: joinAddressPrefix,
  handle: joinHandle,
  handleError: joinHandleError,
  saveHandle: saveJoinHandle,
  reset: resetJoinLink,
  setApproval: setJoinApproval,
  setVisibility: setJoinVisibility,
  copy: copyJoinLink,
} = useTeamJoinLinkCard(
  teamData,
  (team) => (teamData.value = team),
  () => canBringPeopleIn.value
)

const updateActiveTabFromRoute = () => {
  if (route.query.tab && ['members', 'requests', 'invitations'].includes(route.query.tab as string)) {
    activeTab.value = route.query.tab as string
  }

  if (route.query.applicationId) {
    if (route.query.type === 'invitation') {
      activeTab.value = 'invitations'
    } else {
      activeTab.value = 'requests'
    }
  }

  // 首页侧栏团队那一行的「邀请成员」带着 ?invite=1 过来：直接打开邀请框。
  if (route.query.invite === '1' && canBringPeopleIn.value) isInviteDialogActive.value = true
}

onMounted(() => {
  const teamId = teamData.value!.id
  fetchTeamMembers(teamId)

  updateActiveTabFromRoute()
})
watch(() => route.query.invite, updateActiveTabFromRoute)

watch(
  [teamData, activeTab],
  ([newTeamData, newTab]) => {
    if (!newTeamData || !canBringPeopleIn.value) return

    const teamId = teamData.value!.id

    if (newTab === 'requests') {
      fetchJoinRequests(teamId)
    } else if (newTab === 'invitations') {
      fetchTeamInvitations(teamId)
    }
  },
  { immediate: true }
)

watch(
  () => route.query,
  () => {
    updateActiveTabFromRoute()
  },
  { immediate: true }
)

// 按完整的用户名或邮箱找人，邀请查到的那一位。以前这里是一格 UID：没人知道别人的
// UID，打进去的用户名原样当 userId 发出去，被参数校验挡回来；以数字开头的用户名还会
// 被截成一个数，请到另一个人。
const {
  query: inviteQuery,
  found,
  lookingUp,
  lookupError,
  reset: resetInviteLookup,
} = useAccountLookup((e) =>
  e instanceof ApiError && e.status === 404
    ? t('teams.members.inviteNotFound')
    : e instanceof Error && e.message
      ? e.message
      : t('teams.members.inviteLookupFailed')
)
const inviteRoleInput = ref('MEMBER')
const inviteMessageInput = ref('')

const joinRequests = ref<TeamMembershipApplication[]>([])
// 正在批准或拒绝的那条申请：回话之前两个按钮都按不动，连点只发一次。
const answering = ref<number>()
const loadingRequests = ref(false)
const failedRequests = ref(false)
const requestsError = ref<string | null>(null)

const teamInvitations = ref<TeamMembershipApplication[]>([])
const loadingInvitations = ref(false)
const failedInvitations = ref(false)
const invitationsError = ref<string | null>(null)

const fetchTeamMembers = async (teamId: number) => {
  failedMembers.value = false
  membersError.value = null
  try {
    const {
      data: { members },
    } = await TeamsApi.getMembers(teamId)
    teamMembers.value = members
  } catch (error) {
    console.error('Failed to load team members', error)
    failedMembers.value = true
    membersError.value = error instanceof Error && error.message ? error.message : null
  }
}

// 重试时重新拿这一格：团队 id 从注入的 teamData 来。
const retryMembers = () => {
  if (teamData.value) fetchTeamMembers(teamData.value.id)
}

const fetchJoinRequests = async (teamId: number) => {
  loadingRequests.value = true
  failedRequests.value = false
  requestsError.value = null
  try {
    const response = await TeamsApi.listTeamJoinRequests(teamId)
    joinRequests.value = response.data.applications
  } catch (error) {
    console.error('Failed to load join requests', error)
    failedRequests.value = true
    requestsError.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loadingRequests.value = false
  }
}

const retryRequests = () => {
  if (teamData.value) fetchJoinRequests(teamData.value.id)
}

const fetchTeamInvitations = async (teamId: number) => {
  loadingInvitations.value = true
  failedInvitations.value = false
  invitationsError.value = null
  try {
    const response = await TeamsApi.listTeamInvitations(teamId)
    teamInvitations.value = response.data.invitations
  } catch (error) {
    console.error('Failed to load sent invitations', error)
    failedInvitations.value = true
    invitationsError.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loadingInvitations.value = false
  }
}

const retryInvitations = () => {
  if (teamData.value) fetchTeamInvitations(teamData.value.id)
}

// 下面几个操作成功时有的回 204，响应体是空串：失败只认 withErrorHandling 给的 undefined，
// 拿真假判断会把成功当失败——不提示、不刷新，那一行还挂着，再点一次就是「找不到」。
const confirmInvite = async () => {
  const invitee = found.value
  if (!teamData.value || !invitee) {
    return
  }

  const result = await errorHandler.withErrorHandling(async () => {
    return await TeamsApi.createInvitation(teamData.value!.id, {
      userId: invitee.id,
      role: inviteRoleInput.value as any,
      message: inviteMessageInput.value || undefined,
    })
  })

  if (result !== undefined) {
    toast.success(t('teams.members.inviteSent'))
    resetInviteLookup()
    inviteRoleInput.value = 'MEMBER'
    inviteMessageInput.value = ''
    isInviteDialogActive.value = false
    await fetchTeamInvitations(teamData.value!.id)
  }
}

const promoteToAdmin = async (userId: number) => {
  if (!teamData.value) {
    return
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.updateMember(teamData.value!.id, userId, { role: 'ADMIN' })
    },
    { defaultMessage: t('teams.members.promoteFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.promoteDone'))
    await fetchTeamMembers(teamData.value!.id)
  }
}

const demoteToMember = async (userId: number) => {
  if (!teamData.value) {
    return
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.updateMember(teamData.value!.id, userId, { role: 'MEMBER' })
    },
    { defaultMessage: t('teams.members.demoteFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.demoteDone'))
    await fetchTeamMembers(teamData.value!.id)
  }
}

const removeMember = async (userId: number) => {
  if (!teamData.value) {
    return
  }

  // 移出后他不再能进这个团队，先确认（§3.7）：行里的入口是灰的，红只出现在这一下确认上。
  const name = teamMembers.value.find((member) => member.user.id === userId)?.user.nickname ?? ''
  const confirmed = await dialog
    .confirm(t('teams.members.removeBody'), {
      title: t('teams.members.removeTitle', { name }),
      confirmLabel: t('teams.members.remove'),
      danger: true,
    })
    .wait()
    .catch(() => false)
  if (!confirmed) {
    return
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.removeMember(teamData.value!.id, userId)
    },
    { defaultMessage: t('teams.members.removeFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.removeDone'))
    await fetchTeamMembers(teamData.value!.id)
  }
}

const approveRequest = async (requestId: number) => {
  if (!teamData.value || answering.value !== undefined) return

  answering.value = requestId
  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.approveJoinRequest(teamData.value!.id, requestId)
    },
    { defaultMessage: t('teams.members.approveFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.approveDone'))
    // 刷新数据
    await Promise.all([fetchJoinRequests(teamData.value!.id), fetchTeamMembers(teamData.value!.id)])
  }
  answering.value = undefined
}

const rejectRequest = async (requestId: number) => {
  if (!teamData.value || answering.value !== undefined) return

  answering.value = requestId
  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.rejectJoinRequest(teamData.value!.id, requestId)
    },
    { defaultMessage: t('teams.members.rejectFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.rejectDone'))
    // 刷新数据
    await fetchJoinRequests(teamData.value!.id)
  }
  answering.value = undefined
}

const cancelInvitation = async (invitationId: number) => {
  if (!teamData.value) return

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.cancelInvitation(teamData.value!.id, invitationId)
    },
    { defaultMessage: t('teams.members.cancelFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.cancelDone'))
    // 刷新数据
    await fetchTeamInvitations(teamData.value!.id)
  }
}
</script>
