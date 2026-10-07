<template>
  <div>
    <!-- 操作区 -->
    <div class="d-flex align-center mb-4">
      <v-spacer></v-spacer>
      <BaseButton
        v-if="canBringPeopleIn"
        kind="primary"
        prepend-icon="mdi-account-plus"
        size="sm"
        @click="isInviteDialogActive = true"
      >
        {{ t('teams.members.invite') }}
      </BaseButton>

      <AdaptiveDialog
        v-model="isInviteDialogActive"
        :title="t('teams.members.invite')"
        size="md"
        :primary-label="t('teams.members.inviteSubmit')"
        :primary-disabled="!found"
        @primary="confirmInvite"
      >
        <v-form @submit.prevent="confirmInvite">
          <v-text-field
            v-model="inviteQuery"
            autocomplete="off"
            :label="t('teams.members.inviteLabel')"
            :placeholder="t('teams.members.invitePlaceholder')"
            variant="outlined"
            :loading="lookingUp"
            :error-messages="lookupError ? [lookupError] : []"
            :hide-details="!lookupError"
            class="mb-4"
          />
          <!-- 先把查到的人摆出来：邀请的是这一位，按下按钮之前就看得见。 -->
          <div v-if="found" class="d-flex align-center mb-4" data-testid="found-user">
            <UserAvatar
              :name="found.name || found.handle"
              :avatar="getAvatarUrl(found.avatar_id)"
              :seed="found.handle"
              :size="32"
              class="mr-3"
            />
            <div class="min-w-0">
              <div class="t-body">{{ found.name || found.handle }}</div>
              <div class="t-meta c-muted">{{ found.handle }}</div>
            </div>
          </div>
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
        </v-form>
      </AdaptiveDialog>
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
        <BaseLoadError
          v-if="failedMembers"
          :title="t('teams.members.loadMembersFailed')"
          :error="membersError"
          @retry="retryMembers"
        />

        <v-card v-if="!failedMembers" flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="member in teamMembers"
              :key="member.user.id"
              class="member-item"
              @contextmenu="memberActions(member).length && rowMenu.open(member.user.id, $event)"
            >
              <AdaptiveMenu
                v-if="memberActions(member).length"
                v-bind="rowMenu.bind(member.user.id)"
                :actions="memberActions(member)"
                :title="member.user.nickname"
              >
                <template #activator />
              </AdaptiveMenu>
              <template #prepend>
                <UserAvatar
                  :avatar="getAvatarUrl(member.user.avatarId)"
                  :name="member.user.nickname"
                  :seed="member.user.username"
                  size="40"
                  class="mr-3"
                />
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
              <!-- What this role can actually do. A bare 队长/管理员 label leaves the
                   difference between an admin and a member unstated. -->
              <v-list-item-subtitle>{{ memberRoleNote(member.role) }}</v-list-item-subtitle>
              <template #append>
                <div class="d-flex align-center">
                  <v-tooltip v-if="isSelfOwner && member.role === 'MEMBER'" location="bottom">
                    <template #activator="{ props: activatorProps }">
                      <BaseButton
                        v-bind="activatorProps"
                        icon="mdi-account-arrow-up"
                        size="sm"
                        :aria-label="t('teams.members.promote')"
                        @click="promoteToAdmin(member.user.id)"
                      />
                    </template>
                    <span>{{ t('teams.members.promote') }}</span>
                  </v-tooltip>
                  <v-tooltip v-if="isSelfOwner && member.role === 'ADMIN'" location="bottom">
                    <template #activator="{ props: activatorProps }">
                      <BaseButton
                        v-bind="activatorProps"
                        icon="mdi-account-arrow-down"
                        size="sm"
                        :aria-label="t('teams.members.demote')"
                        @click="demoteToMember(member.user.id)"
                      />
                    </template>
                    <span>{{ t('teams.members.demote') }}</span>
                  </v-tooltip>
                  <v-tooltip v-if="isSelfAdmin && member.role !== 'OWNER'" location="bottom">
                    <template #activator="{ props: activatorProps }">
                      <BaseButton
                        v-bind="activatorProps"
                        kind="ghost"
                        icon="mdi-delete"
                        size="sm"
                        :aria-label="t('teams.members.remove')"
                        @click="removeMember(member.user.id)"
                      />
                    </template>
                    <span>{{ t('teams.members.remove') }}</span>
                  </v-tooltip>
                </div>
              </template>
            </v-list-item>
          </v-list>
        </v-card>

        <BaseEmptyState
          v-if="!failedMembers && teamMembers.length === 0"
          icon="mdi-account-group"
          :title="t('teams.members.emptyMembers')"
          :desc="t('teams.members.emptyMembersHint')"
        />
      </v-window-item>

      <!-- 加入申请 -->
      <v-window-item value="requests">
        <v-card v-if="loadingRequests" flat class="d-flex justify-center align-center pa-6">
          <v-progress-circular indeterminate color="primary"></v-progress-circular>
        </v-card>

        <BaseLoadError
          v-else-if="failedRequests"
          :title="t('teams.members.loadRequestsFailed')"
          :error="requestsError"
          @retry="retryRequests"
        />

        <BaseEmptyState
          v-else-if="joinRequests.length === 0"
          icon="mdi-account-arrow-right"
          :title="t('teams.members.emptyRequests')"
          :desc="t('teams.members.emptyRequestsHint')"
        />

        <v-card v-else flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="request in joinRequests"
              :key="request.id"
              class="request-item"
              :class="{ 'pending-request': request.status === 'PENDING' }"
            >
              <template #prepend>
                <UserAvatar
                  :avatar="getAvatarUrl(request.user.avatarId)"
                  :name="request.user.nickname"
                  :seed="request.user.username"
                  size="40"
                  class="mr-3"
                />
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
                  <BaseButton
                    kind="primary"
                    size="sm"
                    prepend-icon="mdi-check"
                    class="mr-2"
                    :loading="answering === request.id"
                    :disabled="answering !== undefined"
                    @click="approveRequest(request.id)"
                  >
                    {{ t('teams.members.approve') }}
                  </BaseButton>
                  <BaseButton
                    kind="danger"
                    size="sm"
                    prepend-icon="mdi-close"
                    :disabled="answering !== undefined"
                    @click="rejectRequest(request.id)"
                  >
                    {{ t('teams.members.reject') }}
                  </BaseButton>
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

        <BaseLoadError
          v-else-if="failedInvitations"
          :title="t('teams.members.loadInvitationsFailed')"
          :error="invitationsError"
          @retry="retryInvitations"
        />

        <BaseEmptyState
          v-else-if="teamInvitations.length === 0"
          icon="mdi-email-outline"
          :title="t('teams.members.emptyInvitations')"
          :desc="t('teams.members.emptyInvitationsHint')"
        />

        <v-card v-else flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="invitation in teamInvitations"
              :key="invitation.id"
              class="invitation-item"
              :class="{ 'pending-invitation': invitation.status === 'PENDING' }"
            >
              <template #prepend>
                <UserAvatar
                  :avatar="getAvatarUrl(invitation.user.avatarId)"
                  :name="invitation.user.nickname"
                  :seed="invitation.user.username"
                  size="40"
                  class="mr-3"
                />
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
                <BaseButton
                  v-if="invitation.status === 'PENDING'"
                  kind="ghost"
                  size="sm"
                  icon="mdi-delete"
                  :aria-label="t('global.cancel')"
                  @click="cancelInvitation(invitation.id)"
                />
              </template>
            </v-list-item>
          </v-list>
        </v-card>
      </v-window-item>
    </v-window>
  </div>
</template>

<script setup lang="ts">
import type { MenuAction } from '@/components/common/menuAction'
import type { Team, TeamMember, TeamMembershipApplication } from '@/types'

import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { getAvatarUrl } from '@/utils/materials'

import { useAccountLookup } from '@/composables/useAccountLookup'
import { useRowMenu } from '@/composables/useRowMenu'

import TeamJoinLinkCard from './TeamJoinLinkCard.vue'

import { ApiError } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import i18n, { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { TeamsApi } from '@/network/api/teams'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'

const { locale } = i18n.global
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

// 右键一位成员：行尾那几颗（升管理员、降成员、移出）收成一份，弹在鼠标那一点上。
// 谁看得见哪一项和那几颗按钮同一套判据。
const rowMenu = useRowMenu<number>()
function memberActions(member: TeamMember): MenuAction[] {
  const actions: MenuAction[] = []
  if (isSelfOwner.value && member.role === 'MEMBER')
    actions.push({
      key: 'promote',
      label: t('teams.members.promote'),
      icon: 'mdi-account-arrow-up',
      onSelect: () => void promoteToAdmin(member.user.id),
    })
  if (isSelfOwner.value && member.role === 'ADMIN')
    actions.push({
      key: 'demote',
      label: t('teams.members.demote'),
      icon: 'mdi-account-arrow-down',
      onSelect: () => void demoteToMember(member.user.id),
    })
  if (isSelfAdmin.value && member.role !== 'OWNER')
    actions.push({
      key: 'remove',
      label: t('teams.members.remove'),
      icon: 'mdi-delete',
      danger: true,
      onSelect: () => void removeMember(member.user.id),
    })
  return actions
}
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

const roleOptions = computed(() => [
  { title: t('teams.members.roleMember'), value: 'MEMBER', props: { subtitle: t('teams.members.roleMemberHint') } },
  { title: t('teams.members.roleAdmin'), value: 'ADMIN', props: { subtitle: t('teams.members.roleAdminHint') } },
])

/** What the role can do, for the member-list rows. */
function roleHint(role?: string): string {
  if (role === 'OWNER') return t('teams.members.roleOwnerHint')
  if (role === 'ADMIN') return t('teams.members.roleAdminHint')
  return t('teams.members.roleMemberHint')
}

/** Owner and admin rows already carry a role chip; a plain member carries none, so its
 *  note spells the role out too. */
function memberRoleNote(role?: string): string {
  if (role === 'OWNER' || role === 'ADMIN') return roleHint(role)
  return `${t('teams.members.roleMember')} · ${roleHint(role)}`
}

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

const pendingRequests = computed(() => {
  return joinRequests.value.filter((request) => request.status === 'PENDING')
})

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
