<script setup lang="ts">
import type { MenuAction } from '@/components/common/menuAction'
import type { LookedUpUser } from '@/api'
import type { TeamMember, TeamMembershipApplication } from '@/types'

import { computed } from 'vue'

import { useRowMenu } from '@/composables/useRowMenu'
import { getAvatarUrl } from '@/utils/materials'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import i18n, { t } from '@/i18n'

// 小队成员这一屏的画法：三格列表（成员 / 加入申请 / 已发送邀请）加一个邀请框，只吃数据、
// 往上报动作。取数与写入都在页面里（`Members.vue`）—— 所以「谁在管理这个团队」由页面算好
// 递上来，删一位之前的确认框也留在那一层。这一半一行取数都没有。
//
// 加入链接那张卡（`TeamJoinLinkCard`）也是只吃数据、往上报动作的：页面把数据摆好，从下面
// 那个具名插槽递进来，摆在哪由这里说了算。
defineOptions({ name: 'MembersView' })

const props = defineProps<{
  /** 读者是这个团队的所有者：能升管理员、能降回成员。 */
  isSelfOwner: boolean
  /** 读者能往团队里带人（管理员以上，且不是自己名下那种单人小队）：邀请按钮与两张页签。 */
  canBringPeopleIn: boolean
  members: TeamMember[]
  /** 成员这一格没读出来：这一格换成失败态，不画成「还没有成员」。 */
  failedMembers: boolean
  membersError: string | null
  joinRequests: TeamMembershipApplication[]
  loadingRequests: boolean
  failedRequests: boolean
  requestsError: string | null
  /** 正在批准或拒绝的那条申请：回话之前两个按钮都按不动，连点只发一次。 */
  answering?: number
  invitations: TeamMembershipApplication[]
  loadingInvitations: boolean
  failedInvitations: boolean
  invitationsError: string | null
  /** 按用户名或邮箱查到的这一位。查询本身在页面里（`useAccountLookup`）。 */
  found: LookedUpUser | null
  lookingUp: boolean
  lookupError: string | null
  /** 查到的那一位已经在团队里：邀请框说一声，那颗「邀请」也按不动。 */
  alreadyIn: boolean
}>()

const emit = defineEmits<{
  retryMembers: []
  promote: [userId: number]
  demote: [userId: number]
  removeMember: [userId: number]
  retryRequests: []
  approveRequest: [requestId: number]
  rejectRequest: [requestId: number]
  retryInvitations: []
  cancelInvitation: [invitationId: number]
  /** 邀请框里按了「邀请」：这一位要不要真发出去由页面决定。 */
  confirmInvite: []
}>()

const activeTab = defineModel<string>('activeTab', { required: true })
const isInviteDialogActive = defineModel<boolean>('isInviteDialogActive', { required: true })
const inviteQuery = defineModel<string>('inviteQuery', { required: true })
const inviteRoleInput = defineModel<string>('inviteRoleInput', { required: true })
const inviteMessageInput = defineModel<string>('inviteMessageInput', { required: true })

const { locale } = i18n.global

// 右键一位成员：行尾那几颗（升管理员、降成员、移出）收成一份，弹在鼠标那一点上。
// 谁看得见哪一项和那几颗按钮同一套判据。移出看服务端给的 canRemove：能不能移出取决于
// 读者和那一位各是什么角色（管理员移不了别的管理员），由移出接口用的同一条规则算出来，
// 这里不再另写一份。
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
  if (member.canRemove)
    actions.push({
      key: 'remove',
      label: t('teams.members.remove'),
      icon: 'mdi-delete',
      danger: true,
      onSelect: () => emit('removeMember', member.user.id),
    })
  return actions
}

const pendingRequests = computed(() => props.joinRequests.filter((request) => request.status === 'PENDING'))

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

<template>
  <div>
    <!-- 操作区 -->
    <div class="d-flex align-center mb-4">
      <v-spacer></v-spacer>
      <v-dialog v-model="isInviteDialogActive" max-width="500">
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
          <v-form @submit.prevent="emit('confirmInvite')">
            <v-card :title="t('teams.members.invite')">
              <v-card-text>
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
                  <v-spacer />
                  <span v-if="alreadyIn" class="t-meta c-muted">{{ t('teams.members.alreadyIn') }}</span>
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
              </v-card-text>

              <v-card-actions>
                <v-spacer></v-spacer>
                <BaseButton type="button" @click="isActive.value = false">{{ t('teams.members.cancel') }}</BaseButton>
                <BaseButton type="submit" kind="primary" :disabled="!found || alreadyIn">{{
                  t('teams.members.inviteSubmit')
                }}</BaseButton>
              </v-card-actions>
            </v-card>
          </v-form>
        </template>
      </v-dialog>
    </div>

    <!-- 加入链接那张卡：数据在页面那一层，由页面从这个口子递进来。 -->
    <slot name="join-link" />

    <!-- 标签页 -->
    <v-tabs v-model="activeTab" color="primary" class="mb-4">
      <v-tab value="members">{{ t('teams.members.tabMembers') }}</v-tab>
      <v-tab v-if="canBringPeopleIn" value="requests">
        {{ t('teams.members.tabRequests') }}
        <v-badge
          v-if="pendingRequests.length > 0"
          :content="pendingRequests.length"
          color="error"
          inline
          class="ml-1"
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
          @retry="emit('retryMembers')"
        />

        <v-card v-if="!failedMembers" flat rounded="lg">
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
              <!-- What this role can actually do. A bare 所有者/管理员 label leaves the
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
                        @click="emit('promote', member.user.id)"
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
                        @click="emit('demote', member.user.id)"
                      />
                    </template>
                    <span>{{ t('teams.members.demote') }}</span>
                  </v-tooltip>
                  <v-tooltip v-if="member.canRemove" location="bottom">
                    <template #activator="{ props: activatorProps }">
                      <BaseButton
                        v-bind="activatorProps"
                        kind="ghost"
                        icon="mdi-delete"
                        size="sm"
                        :aria-label="t('teams.members.remove')"
                        @click="emit('removeMember', member.user.id)"
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
          v-if="!failedMembers && members.length === 0"
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
          @retry="emit('retryRequests')"
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
                    @click="emit('approveRequest', request.id)"
                  >
                    {{ t('teams.members.approve') }}
                  </BaseButton>
                  <BaseButton
                    kind="danger"
                    size="sm"
                    prepend-icon="mdi-close"
                    :disabled="answering !== undefined"
                    @click="emit('rejectRequest', request.id)"
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
          @retry="emit('retryInvitations')"
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
                  @click="emit('cancelInvitation', invitation.id)"
                />
              </template>
            </v-list-item>
          </v-list>
        </v-card>
      </v-window-item>
    </v-window>
  </div>
</template>

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
