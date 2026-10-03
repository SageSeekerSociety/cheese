<template>
  <div>
    <!-- 操作区 -->
    <div class="d-flex align-center mb-4">
      <v-spacer></v-spacer>
      <v-dialog v-model="isInviteDialogActive" max-width="500">
        <template #activator="{ props: activatorProps }">
          <v-btn
            v-if="canBringPeopleIn"
            v-bind="activatorProps"
            color="primary"
            prepend-icon="mdi-account-plus"
            size="small"
          >
            {{ t('teams.members.invite') }}
          </v-btn>
        </template>

        <template #default="{ isActive }">
          <v-form @submit.prevent="confirmInvite">
            <v-card :title="t('teams.members.invite')">
              <v-card-text>
                <div class="text-caption mb-2">{{ t('teams.members.inviteUidHint') }}</div>
                <v-text-field
                  v-model.number="inviteUidInput"
                  autocomplete="off"
                  label="UID"
                  variant="outlined"
                  hide-details
                  class="mb-4"
                />
                <v-select
                  v-model="inviteRoleInput"
                  autocomplete="off"
                  :items="roleOptions"
                  :label="t('teams.members.roleLabel')"
                  variant="outlined"
                  hide-details
                  class="mb-4"
                ></v-select>
                <v-textarea
                  v-model="inviteMessageInput"
                  autocomplete="off"
                  :label="t('teams.members.inviteMessageLabel')"
                  variant="outlined"
                  :placeholder="t('teams.members.inviteMessagePlaceholder')"
                  rows="3"
                  auto-grow
                  hide-details
                ></v-textarea>
              </v-card-text>

              <v-card-actions>
                <v-spacer></v-spacer>
                <v-btn type="button" variant="text" @click="isActive.value = false">{{
                  t('teams.members.cancel')
                }}</v-btn>
                <v-btn type="submit" color="primary" variant="tonal">{{ t('teams.members.inviteSubmit') }}</v-btn>
              </v-card-actions>
            </v-card>
          </v-form>
        </template>
      </v-dialog>
    </div>

    <TeamJoinLinkCard
      v-if="teamData && canBringPeopleIn"
      :team="teamData"
      @updated="(team: Team) => (teamData = team)"
    />

    <!-- 标签页 -->
    <v-tabs v-model="activeTab" color="primary" class="mb-4">
      <v-tab value="members">{{ t('teams.members.tabMembers') }}</v-tab>
      <v-tab v-if="canBringPeopleIn" value="requests">
        {{ t('teams.members.tabRequests') }}
        <v-badge
          v-if="pendingRequests.length > 0"
          :content="pendingRequests.length"
          color="error"
          class="ml-2"
        ></v-badge>
      </v-tab>
      <v-tab v-if="canBringPeopleIn" value="invitations">{{ t('teams.members.tabInvitations') }}</v-tab>
    </v-tabs>

    <v-window v-model="activeTab">
      <!-- 成员列表 -->
      <v-window-item value="members">
        <v-card flat rounded="lg">
          <v-list>
            <v-list-item v-for="member in teamMembers" :key="member.user.id" class="member-item">
              <template #prepend>
                <v-avatar size="40" rounded="circle" color="surface-variant" class="mr-3">
                  <v-img :src="getAvatarUrl(member.user.avatarId)" />
                </v-avatar>
              </template>
              <v-list-item-title class="font-weight-medium">
                {{ member.user.nickname }}
                <v-chip v-if="member.role === 'OWNER'" color="primary" size="small" variant="tonal" class="ml-2">{{
                  t('teams.members.roleOwner')
                }}</v-chip>
                <v-chip
                  v-else-if="member.role === 'ADMIN'"
                  color="secondary"
                  size="small"
                  variant="tonal"
                  class="ml-2"
                  >{{ t('teams.members.roleAdmin') }}</v-chip
                >
              </v-list-item-title>
              <template #append>
                <div class="d-flex align-center">
                  <v-tooltip v-if="isSelfOwner && member.role === 'MEMBER'" location="bottom">
                    <template #activator="{ props: activatorProps }">
                      <v-btn
                        v-bind="activatorProps"
                        icon="mdi-account-arrow-up"
                        variant="text"
                        color="primary"
                        size="small"
                        @click="promoteToAdmin(member.user.id)"
                      ></v-btn>
                    </template>
                    <span>{{ t('teams.members.promote') }}</span>
                  </v-tooltip>
                  <v-tooltip v-if="isSelfOwner && member.role === 'ADMIN'" location="bottom">
                    <template #activator="{ props: activatorProps }">
                      <v-btn
                        v-bind="activatorProps"
                        icon="mdi-account-arrow-down"
                        variant="text"
                        color="primary"
                        size="small"
                        @click="demoteToMember(member.user.id)"
                      ></v-btn>
                    </template>
                    <span>{{ t('teams.members.demote') }}</span>
                  </v-tooltip>
                  <v-tooltip v-if="isSelfAdmin && member.role !== 'OWNER'" location="bottom">
                    <template #activator="{ props: activatorProps }">
                      <v-btn
                        v-bind="activatorProps"
                        icon="mdi-delete"
                        variant="text"
                        color="error"
                        size="small"
                        @click="removeMember(member.user.id)"
                      ></v-btn>
                    </template>
                    <span>{{ t('teams.members.remove') }}</span>
                  </v-tooltip>
                </div>
              </template>
            </v-list-item>
          </v-list>
        </v-card>

        <!-- 无成员时的提示 -->
        <div v-if="teamMembers.length === 0" class="text-center py-12">
          <v-icon icon="mdi-account-group" size="64" class="mb-4 empty-state-icon"></v-icon>
          <h3 class="text-h6 font-weight-medium mb-2">{{ t('teams.members.emptyMembers') }}</h3>
          <p class="text-body-2 text-medium-emphasis mb-6">{{ t('teams.members.emptyMembersHint') }}</p>
        </div>
      </v-window-item>

      <!-- 加入申请 -->
      <v-window-item value="requests">
        <v-card v-if="loadingRequests" flat class="d-flex justify-center align-center pa-6">
          <v-progress-circular indeterminate color="primary"></v-progress-circular>
        </v-card>

        <v-card v-else-if="joinRequests.length === 0" flat class="text-center py-12">
          <v-icon icon="mdi-account-arrow-right" size="64" class="mb-4 empty-state-icon"></v-icon>
          <h3 class="text-h6 font-weight-medium mb-2">{{ t('teams.members.emptyRequests') }}</h3>
          <p class="text-body-2 text-medium-emphasis">{{ t('teams.members.emptyRequestsHint') }}</p>
        </v-card>

        <v-card v-else flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="request in joinRequests"
              :key="request.id"
              class="request-item"
              :class="{ 'pending-request': request.status === 'PENDING' }"
            >
              <template #prepend>
                <v-avatar size="40" rounded="circle" color="surface-variant" class="mr-3">
                  <v-img :src="getAvatarUrl(request.user.avatarId)" />
                </v-avatar>
              </template>
              <v-list-item-title class="font-weight-medium">
                {{ request.user.nickname }}
                <v-chip :color="getStatusColor(request.status)" size="x-small" variant="tonal" class="ml-2">
                  {{ getStatusText(request.status) }}
                </v-chip>
              </v-list-item-title>
              <v-list-item-subtitle v-if="request.message" class="text-caption text-truncate">
                {{ request.message }}
              </v-list-item-subtitle>
              <v-list-item-subtitle class="text-caption">
                {{ t('teams.members.requestedAt', { time: formatDate(request.createdAt) }) }}
              </v-list-item-subtitle>

              <template #append>
                <div v-if="request.status === 'PENDING'" class="d-flex">
                  <v-btn
                    variant="text"
                    color="success"
                    size="small"
                    prepend-icon="mdi-check"
                    class="mr-2"
                    :loading="answering === request.id"
                    :disabled="answering !== undefined"
                    @click="approveRequest(request.id)"
                  >
                    {{ t('teams.members.approve') }}
                  </v-btn>
                  <v-btn
                    variant="text"
                    color="error"
                    size="small"
                    prepend-icon="mdi-close"
                    :disabled="answering !== undefined"
                    @click="rejectRequest(request.id)"
                  >
                    {{ t('teams.members.reject') }}
                  </v-btn>
                </div>
                <div
                  v-else-if="request.status === 'APPROVED' || request.status === 'REJECTED'"
                  class="text-caption text-medium-emphasis"
                >
                  <i18n-t
                    v-if="request.processedBy?.nickname"
                    scope="global"
                    keypath="teams.members.processedBy"
                    tag="span"
                  >
                    <template #name>
                      <UserRef
                        :handle="request.processedBy.username"
                        :name="request.processedBy.nickname"
                        :project-id="null"
                      />
                    </template>
                  </i18n-t>
                  <template v-else>{{ t('teams.members.processed') }}</template>
                </div>
              </template>
            </v-list-item>
          </v-list>
        </v-card>
      </v-window-item>

      <!-- 已发送邀请 -->
      <v-window-item value="invitations">
        <v-card v-if="loadingInvitations" flat class="d-flex justify-center align-center pa-6">
          <v-progress-circular indeterminate color="primary"></v-progress-circular>
        </v-card>

        <v-card v-else-if="teamInvitations.length === 0" flat class="text-center py-12">
          <v-icon icon="mdi-email-outline" size="64" class="mb-4 empty-state-icon"></v-icon>
          <h3 class="text-h6 font-weight-medium mb-2">{{ t('teams.members.emptyInvitations') }}</h3>
          <p class="text-body-2 text-medium-emphasis">{{ t('teams.members.emptyInvitationsHint') }}</p>
        </v-card>

        <v-card v-else flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="invitation in teamInvitations"
              :key="invitation.id"
              class="invitation-item"
              :class="{ 'pending-invitation': invitation.status === 'PENDING' }"
            >
              <template #prepend>
                <v-avatar size="40" rounded="circle" color="surface-variant" class="mr-3">
                  <v-img :src="getAvatarUrl(invitation.user.avatarId)" />
                </v-avatar>
              </template>
              <v-list-item-title class="font-weight-medium">
                {{ invitation.user.nickname }}
                <v-chip :color="getStatusColor(invitation.status)" size="x-small" variant="tonal" class="ml-2">
                  {{ getStatusText(invitation.status) }}
                </v-chip>
              </v-list-item-title>
              <v-list-item-subtitle v-if="invitation.message" class="text-caption text-truncate">
                {{ invitation.message }}
              </v-list-item-subtitle>
              <v-list-item-subtitle class="text-caption">
                {{ t('teams.members.invitedAt', { time: formatDate(invitation.createdAt) }) }}
              </v-list-item-subtitle>

              <template #append>
                <v-btn
                  v-if="invitation.status === 'PENDING'"
                  variant="text"
                  color="error"
                  size="small"
                  icon="mdi-delete"
                  @click="cancelInvitation(invitation.id)"
                ></v-btn>
              </template>
            </v-list-item>
          </v-list>
        </v-card>
      </v-window-item>
    </v-window>
  </div>
</template>

<script setup lang="ts">
import type { Team, TeamMember, TeamMembershipApplication } from '@/types'

import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import TeamJoinLinkCard from './TeamJoinLinkCard.vue'

import UserRef from '@/components/common/UserRefLink.vue'
import i18n, { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { TeamsApi } from '@/network/api/teams'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'

const { locale } = i18n.global
const route = useRoute()
const isInviteDialogActive = ref(false)
const teamMembers = ref<TeamMember[]>([])
const teamData = inject(teamDataInjectionKey, ref())

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

const inviteUidInput = ref<number>()
const inviteRoleInput = ref('MEMBER')
const inviteMessageInput = ref('')

const joinRequests = ref<TeamMembershipApplication[]>([])
// 正在批准或拒绝的那条申请：回话之前两个按钮都按不动，连点只发一次。
const answering = ref<number>()
const loadingRequests = ref(false)

const teamInvitations = ref<TeamMembershipApplication[]>([])
const loadingInvitations = ref(false)

const roleOptions = computed(() => [
  { title: t('teams.members.roleMember'), value: 'MEMBER' },
  { title: t('teams.members.roleAdmin'), value: 'ADMIN' },
])

const fetchTeamMembers = async (teamId: number) => {
  const {
    data: { members },
  } = await TeamsApi.getMembers(teamId)
  teamMembers.value = members
}

const fetchJoinRequests = async (teamId: number) => {
  loadingRequests.value = true
  try {
    const response = await TeamsApi.listTeamJoinRequests(teamId)
    joinRequests.value = response.data.applications
  } catch (error) {
    console.error('Failed to load join requests', error)
    toast.error(t('teams.members.loadRequestsFailed'))
  } finally {
    loadingRequests.value = false
  }
}

const fetchTeamInvitations = async (teamId: number) => {
  loadingInvitations.value = true
  try {
    const response = await TeamsApi.listTeamInvitations(teamId)
    teamInvitations.value = response.data.invitations
  } catch (error) {
    console.error('Failed to load sent invitations', error)
    toast.error(t('teams.members.loadInvitationsFailed'))
  } finally {
    loadingInvitations.value = false
  }
}

const pendingRequests = computed(() => {
  return joinRequests.value.filter((request) => request.status === 'PENDING')
})

// 下面几个操作成功时有的回 204，响应体是空串：失败只认 withErrorHandling 给的 undefined，
// 拿真假判断会把成功当失败——不提示、不刷新，那一行还挂着，再点一次就是「找不到」。
const confirmInvite = async () => {
  if (!teamData.value || !inviteUidInput.value) {
    return
  }

  const result = await errorHandler.withErrorHandling(async () => {
    return await TeamsApi.createInvitation(teamData.value!.id, {
      userId: inviteUidInput.value!,
      role: inviteRoleInput.value as any,
      message: inviteMessageInput.value || undefined,
    })
  })

  if (result !== undefined) {
    toast.success(t('teams.members.inviteSent'))
    inviteUidInput.value = undefined
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

const formatDate = (timestamp: number) => {
  return new Date(timestamp).toLocaleString(locale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const getStatusColor = (status: string) => {
  switch (status) {
    case 'PENDING':
      return 'warning'
    case 'APPROVED':
    case 'ACCEPTED':
      return 'success'
    case 'REJECTED':
    case 'DECLINED':
    case 'CANCELED':
      return 'error'
    default:
      return 'grey'
  }
}

const getStatusText = (status: string) => {
  switch (status) {
    case 'PENDING':
      return t('teams.members.status.pending')
    case 'APPROVED':
      return t('teams.members.status.approved')
    case 'ACCEPTED':
      return t('teams.members.status.accepted')
    case 'REJECTED':
      return t('teams.members.status.rejected')
    case 'DECLINED':
      return t('teams.members.status.declined')
    case 'CANCELED':
      return t('teams.members.status.canceled')
    default:
      return t('teams.members.status.unknown')
  }
}
</script>

<style scoped lang="scss">
/* 空状态插图：元信息级别的装饰。--line-2 浅色 #E2E3E6（和原来的
   grey-lighten-2 #E0E0E0 几乎同值），深色 #3A3E45（在深色页面上仍看得出形状）。 */
.empty-state-icon {
  color: var(--line-2);
}

.member-item {
  transition: background-color 0.2s ease;
  border-radius: 8px;
  margin-bottom: 2px;

  &:hover {
    background-color: var(--fill);
  }
}

.request-item,
.invitation-item {
  transition: background-color 0.2s ease;
  border-radius: 8px;
  margin-bottom: 4px;
  border-left: 3px solid transparent;

  &:hover {
    background-color: var(--fill);
  }
}

.pending-request {
  border-left-color: var(--v-theme-warning);
  background-color: rgba(var(--v-theme-warning), 0.05);
}

.pending-invitation {
  border-left-color: var(--v-theme-info);
  background-color: rgba(var(--v-theme-info), 0.05);
}
</style>
