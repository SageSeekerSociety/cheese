<template>
  <div>
    <!-- 操作区 -->
    <div class="d-flex align-center mb-4">
      <v-spacer></v-spacer>
      <v-dialog :model-value="inviteDialog" max-width="500" @update:model-value="$emit('update:inviteDialog', $event)">
        <template #activator="{ props: activatorProps }">
          <BaseButton
            v-if="canBringPeopleIn"
            v-bind="activatorProps"
            kind="primary"
            prepend-icon="mdi-account-plus"
            size="sm"
          >
            {{ t('teams.members.invite') }}
          </BaseButton>
        </template>

        <template #default="{ isActive }">
          <v-form @submit.prevent="$emit('submitInvite')">
            <v-card :title="t('teams.members.invite')">
              <v-card-text>
                <v-text-field
                  :model-value="inviteQuery"
                  autocomplete="off"
                  :label="t('teams.members.inviteLabel')"
                  :placeholder="t('teams.members.invitePlaceholder')"
                  variant="outlined"
                  :loading="inviteLookingUp"
                  :error-messages="inviteLookupError ? [inviteLookupError] : []"
                  :hide-details="!inviteLookupError"
                  class="mb-4"
                  @update:model-value="$emit('update:inviteQuery', $event)"
                />
                <!-- 先把查到的人摆出来：邀请的是这一位，按下按钮之前就看得见。 -->
                <div v-if="inviteFound" class="d-flex align-center mb-4" data-testid="found-user">
                  <UserAvatar
                    :name="inviteFound.name || inviteFound.handle"
                    :avatar="inviteFound.avatar_id == null ? '' : getAvatarUrl(inviteFound.avatar_id)"
                    :size="32"
                    class="mr-3"
                  />
                  <div class="min-w-0">
                    <div class="t-body">{{ inviteFound.name || inviteFound.handle }}</div>
                    <div class="t-meta c-muted">{{ inviteFound.handle }}</div>
                  </div>
                </div>
                <v-select
                  :model-value="inviteRole"
                  autocomplete="off"
                  :items="roleOptions"
                  :label="t('teams.members.roleLabel')"
                  variant="outlined"
                  hide-details
                  class="mb-4"
                  @update:model-value="$emit('update:inviteRole', $event)"
                ></v-select>
                <v-textarea
                  :model-value="inviteMessage"
                  autocomplete="off"
                  :label="t('teams.members.inviteMessageLabel')"
                  variant="outlined"
                  :placeholder="t('teams.members.inviteMessagePlaceholder')"
                  rows="3"
                  auto-grow
                  hide-details
                  @update:model-value="$emit('update:inviteMessage', $event)"
                ></v-textarea>
              </v-card-text>

              <v-card-actions>
                <v-spacer></v-spacer>
                <BaseButton type="button" @click="isActive.value = false">{{ t('teams.members.cancel') }}</BaseButton>
                <BaseButton type="submit" kind="primary" :disabled="!inviteFound">{{
                  t('teams.members.inviteSubmit')
                }}</BaseButton>
              </v-card-actions>
            </v-card>
          </v-form>
        </template>
      </v-dialog>
    </div>

    <slot name="joinLink" />

    <!-- 标签页 -->
    <v-tabs
      :model-value="activeTab"
      color="primary"
      class="mb-4"
      @update:model-value="$emit('update:activeTab', $event)"
    >
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

    <v-window :model-value="activeTab" @update:model-value="$emit('update:activeTab', $event)">
      <!-- 成员列表 -->
      <v-window-item value="members">
        <BaseLoadError
          v-if="membersFailed"
          :title="t('teams.members.loadMembersFailed')"
          :error="membersError"
          @retry="$emit('retryMembers')"
        />

        <v-card v-if="!membersFailed" flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="member in members"
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
                        @click="$emit('promote', member.user.id)"
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
                        @click="$emit('demote', member.user.id)"
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
                        @click="$emit('remove', member.user.id)"
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
          v-if="!membersFailed && members.length === 0"
          icon="mdi-account-group"
          :title="t('teams.members.emptyMembers')"
          :desc="t('teams.members.emptyMembersHint')"
        />
      </v-window-item>

      <!-- 加入申请 -->
      <v-window-item value="requests">
        <v-card v-if="requestsLoading" flat class="d-flex justify-center align-center pa-6">
          <v-progress-circular indeterminate color="primary"></v-progress-circular>
        </v-card>

        <BaseLoadError
          v-else-if="requestsFailed"
          :title="t('teams.members.loadRequestsFailed')"
          :error="requestsError"
          @retry="$emit('retryRequests')"
        />

        <BaseEmptyState
          v-else-if="requests.length === 0"
          icon="mdi-account-arrow-right"
          :title="t('teams.members.emptyRequests')"
          :desc="t('teams.members.emptyRequestsHint')"
        />

        <v-card v-else flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="request in requests"
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
                  <BaseButton
                    kind="primary"
                    size="sm"
                    prepend-icon="mdi-check"
                    class="mr-2"
                    :loading="answering === request.id"
                    :disabled="answering !== undefined"
                    @click="$emit('approve', request.id)"
                  >
                    {{ t('teams.members.approve') }}
                  </BaseButton>
                  <BaseButton
                    kind="danger"
                    size="sm"
                    prepend-icon="mdi-close"
                    :disabled="answering !== undefined"
                    @click="$emit('reject', request.id)"
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
                        :to="resolveUser(request.processedBy.username, null).to"
                        @navigate="$emit('navigate', resolveUser(request.processedBy.username, null).to)"
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
        <v-card v-if="invitationsLoading" flat class="d-flex justify-center align-center pa-6">
          <v-progress-circular indeterminate color="primary"></v-progress-circular>
        </v-card>

        <BaseLoadError
          v-else-if="invitationsFailed"
          :title="t('teams.members.loadInvitationsFailed')"
          :error="invitationsError"
          @retry="$emit('retryInvitations')"
        />

        <BaseEmptyState
          v-else-if="invitations.length === 0"
          icon="mdi-email-outline"
          :title="t('teams.members.emptyInvitations')"
          :desc="t('teams.members.emptyInvitationsHint')"
        />

        <v-card v-else flat rounded="lg">
          <v-list>
            <v-list-item
              v-for="invitation in invitations"
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
                <BaseButton
                  v-if="invitation.status === 'PENDING'"
                  kind="ghost"
                  size="sm"
                  icon="mdi-delete"
                  :aria-label="t('global.cancel')"
                  @click="$emit('cancelInvite', invitation.id)"
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
import type { LookedUpUser } from '@/api'
import type { MenuAction } from '@/components/common/menuAction'
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { TeamMember, TeamMembershipApplication } from '@/types'

import { computed } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRef.vue'
import i18n, { t } from '@/i18n'

const props = defineProps<{
  canBringPeopleIn: boolean
  activeTab: string
  inviteDialog: boolean
  inviteQuery: string
  inviteLookingUp: boolean
  inviteLookupError: string | null
  inviteFound: LookedUpUser | null
  inviteRole: string
  inviteMessage: string
  members: TeamMember[]
  membersFailed: boolean
  membersError: string | null
  requests: TeamMembershipApplication[]
  requestsLoading: boolean
  requestsFailed: boolean
  requestsError: string | null
  answering: number | undefined
  invitations: TeamMembershipApplication[]
  invitationsLoading: boolean
  invitationsFailed: boolean
  invitationsError: string | null
  isSelfOwner: boolean
  isSelfAdmin: boolean
  resolveUser: (handle: string | null | undefined, projectId?: string | null) => ResolvedUserRef
}>()

const emit = defineEmits<{
  'update:activeTab': [value: string]
  'update:inviteDialog': [value: boolean]
  'update:inviteQuery': [value: string]
  'update:inviteRole': [value: string]
  'update:inviteMessage': [value: string]
  retryMembers: []
  retryRequests: []
  retryInvitations: []
  submitInvite: []
  promote: [userId: number]
  demote: [userId: number]
  remove: [userId: number]
  approve: [requestId: number]
  reject: [requestId: number]
  cancelInvite: [invitationId: number]
  navigate: [target: ResolvedUserRef['to']]
}>()

const { locale } = i18n.global

// 右键一位成员：行尾那几颗（升管理员、降成员、移出）收成一份，弹在鼠标那一点上。
// 谁看得见哪一项和那几颗按钮同一套判据。
const rowMenu = useRowMenu<number>()
function memberActions(member: TeamMember): MenuAction[] {
  const actions: MenuAction[] = []
  if (props.isSelfOwner && member.role === 'MEMBER')
    actions.push({
      key: 'promote',
      label: t('teams.members.promote'),
      icon: 'mdi-account-arrow-up',
      onSelect: () => emit('promote', member.user.id),
    })
  if (props.isSelfOwner && member.role === 'ADMIN')
    actions.push({
      key: 'demote',
      label: t('teams.members.demote'),
      icon: 'mdi-account-arrow-down',
      onSelect: () => emit('demote', member.user.id),
    })
  if (props.isSelfAdmin && member.role !== 'OWNER')
    actions.push({
      key: 'remove',
      label: t('teams.members.remove'),
      icon: 'mdi-delete',
      danger: true,
      onSelect: () => emit('remove', member.user.id),
    })
  return actions
}

const roleOptions = computed(() => [
  { title: t('teams.members.roleMember'), value: 'MEMBER', props: { subtitle: t('teams.members.roleMemberHint') } },
  { title: t('teams.members.roleAdmin'), value: 'ADMIN', props: { subtitle: t('teams.members.roleAdminHint') } },
])

const pendingRequests = computed(() => props.requests.filter((request) => request.status === 'PENDING'))

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
