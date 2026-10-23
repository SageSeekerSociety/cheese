<script setup lang="ts">
import type { LookedUpUser } from '@/api'
import type { MenuAction } from '@/components/common/menuAction'
import type { TeamMember, TeamMembershipApplication } from '@/types'

import { computed, nextTick, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import i18n, { t } from '@/i18n'

// 团队设置「成员」那一栏的画法：加入方式、加入申请、成员、已发送邀请四张卡（申请和邀请
// 有内容才出现）加一个邀请框，只吃数据、往上报动作。取数与写入都在页面里（`Members.vue`）—— 所以「谁在管理这个团队」由页面算好
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

/** 通知点进来时要看的那一张（`members` / `requests` / `invitations`）：它出现时滚到那里。 */
const activeTab = defineModel<string>('activeTab', { required: true })
const isInviteDialogActive = defineModel<boolean>('isInviteDialogActive', { required: true })
const inviteQuery = defineModel<string>('inviteQuery', { required: true })
const inviteRoleInput = defineModel<string>('inviteRoleInput', { required: true })
const inviteMessageInput = defineModel<string>('inviteMessageInput', { required: true })

const { locale } = i18n.global

watch(
  () => [activeTab.value, props.joinRequests.length, props.invitations.length] as const,
  async ([tab]) => {
    if (tab === 'members') return
    await nextTick()
    document.getElementById(`team-${tab}`)?.scrollIntoView?.({ block: 'start' })
  },
  { immediate: true }
)

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
const pendingInvitations = computed(() => props.invitations.filter((invitation) => invitation.status === 'PENDING'))

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

/** Every row carries its role as a chip, the plain member's too. */
function roleLabel(role?: string): string {
  if (role === 'OWNER') return t('teams.members.roleOwner')
  if (role === 'ADMIN') return t('teams.members.roleAdmin')
  return t('teams.members.roleMember')
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
  <div class="settings-page">
    <header class="settings-page__head">
      <h1 class="t-page-title">{{ t('home.nav.teamMembers') }}</h1>
      <!-- The page's one main action, on the title's line like every settings page. -->
      <BaseButton
        v-if="canBringPeopleIn"
        kind="primary"
        prepend-icon="mdi-account-plus"
        @click="isInviteDialogActive = true"
      >
        {{ t('teams.members.invite') }}
      </BaseButton>
    </header>

    <v-dialog v-model="isInviteDialogActive" max-width="500">
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

    <!-- 加入方式那张卡：数据在页面那一层，由页面从这个口子递进来。 -->
    <slot name="join-link" />

    <!-- 加入申请：有申请时才有这一张，标题上是还在等的条数。 -->
    <section
      v-if="canBringPeopleIn && (loadingRequests || failedRequests || joinRequests.length)"
      id="team-requests"
      class="settings-card"
    >
      <h2 class="settings-card__title members-card__title">
        {{ t('teams.members.tabRequests') }}
        <span v-if="pendingRequests.length" class="members-card__count">{{ pendingRequests.length }}</span>
      </h2>
      <div v-if="loadingRequests && !joinRequests.length" class="members-card__loading">
        <v-progress-circular indeterminate size="24" color="primary" :aria-label="t('work.teamLink.loading')" />
      </div>
      <BaseLoadError
        v-else-if="failedRequests"
        class="members-card__error"
        :title="t('teams.members.loadRequestsFailed')"
        :error="requestsError"
        @retry="emit('retryRequests')"
      />
      <ul v-else class="members-list">
        <li v-for="request in joinRequests" :key="request.id" class="members-row">
          <UserAvatar
            :avatar="getAvatarUrl(request.user.avatarId)"
            :name="request.user.nickname"
            :seed="request.user.username"
            size="36"
          />
          <div class="members-row__main">
            <div class="members-row__name">
              <span class="members-row__nick">{{ request.user.nickname }}</span>
              <span v-if="request.status !== 'PENDING'" class="members-chip">{{ getStatusText(request.status) }}</span>
            </div>
            <div v-if="request.message" class="members-row__note">{{ request.message }}</div>
            <div class="members-row__note">
              {{ t('teams.members.requestedAt', { time: formatDate(request.createdAt) }) }}
            </div>
          </div>
          <div v-if="request.status === 'PENDING'" class="members-row__actions">
            <BaseButton :disabled="answering !== undefined" @click="emit('rejectRequest', request.id)">
              {{ t('teams.members.reject') }}
            </BaseButton>
            <BaseButton
              kind="secondary"
              :loading="answering === request.id"
              :disabled="answering !== undefined"
              @click="emit('approveRequest', request.id)"
            >
              {{ t('teams.members.approve') }}
            </BaseButton>
          </div>
          <div v-else-if="request.status === 'APPROVED' || request.status === 'REJECTED'" class="members-row__note">
            <i18n-t v-if="request.processedBy?.nickname" scope="global" keypath="teams.members.processedBy" tag="span">
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
        </li>
      </ul>
    </section>

    <section id="team-members" class="settings-card">
      <h2 class="settings-card__title members-card__title">
        {{ t('home.nav.teamMembers') }}
        <span v-if="members.length" class="members-card__count">{{ members.length }}</span>
      </h2>
      <BaseLoadError
        v-if="failedMembers"
        class="members-card__error"
        :title="t('teams.members.loadMembersFailed')"
        :error="membersError"
        @retry="emit('retryMembers')"
      />
      <BaseEmptyState
        v-else-if="members.length === 0"
        size="inline"
        class="settings-empty"
        :title="t('teams.members.emptyMembers')"
      />
      <ul v-else class="members-list">
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- right-click opens the same menu as the row's ⋯ button, which is the keyboard's way -->
        <li
          v-for="member in members"
          :key="member.user.id"
          class="members-row"
          @contextmenu="memberActions(member).length && rowMenu.open(member.user.id, $event)"
        >
          <UserAvatar
            :avatar="getAvatarUrl(member.user.avatarId)"
            :name="member.user.nickname"
            :seed="member.user.username"
            size="36"
          />
          <div class="members-row__main">
            <!-- The name truncates on its own; the role chip beside it never shrinks. -->
            <div class="members-row__name">
              <span class="members-row__nick">{{ member.user.nickname }}</span>
              <span class="members-chip">{{ roleLabel(member.role) }}</span>
            </div>
            <!-- What this role can actually do: a bare 管理员 leaves the difference to a member unstated. -->
            <div class="members-row__note">{{ roleHint(member.role) }}</div>
          </div>
          <AdaptiveMenu
            v-if="memberActions(member).length"
            v-bind="rowMenu.bind(member.user.id)"
            :actions="memberActions(member)"
            :title="member.user.nickname"
          >
            <template #activator="{ props: activatorProps }">
              <BaseButton
                v-bind="activatorProps"
                icon="mdi-dots-horizontal"
                size="sm"
                :aria-label="t('teams.members.actions', { name: member.user.nickname })"
              />
            </template>
          </AdaptiveMenu>
        </li>
      </ul>
    </section>

    <!-- 已发送邀请：有邀请时才有这一张。 -->
    <section
      v-if="canBringPeopleIn && (loadingInvitations || failedInvitations || invitations.length)"
      id="team-invitations"
      class="settings-card"
    >
      <h2 class="settings-card__title members-card__title">
        {{ t('teams.members.tabInvitations') }}
        <span v-if="pendingInvitations.length" class="members-card__count">{{ pendingInvitations.length }}</span>
      </h2>
      <div v-if="loadingInvitations && !invitations.length" class="members-card__loading">
        <v-progress-circular indeterminate size="24" color="primary" :aria-label="t('work.teamLink.loading')" />
      </div>
      <BaseLoadError
        v-else-if="failedInvitations"
        class="members-card__error"
        :title="t('teams.members.loadInvitationsFailed')"
        :error="invitationsError"
        @retry="emit('retryInvitations')"
      />
      <ul v-else class="members-list">
        <li v-for="invitation in invitations" :key="invitation.id" class="members-row">
          <UserAvatar
            :avatar="getAvatarUrl(invitation.user.avatarId)"
            :name="invitation.user.nickname"
            :seed="invitation.user.username"
            size="36"
          />
          <div class="members-row__main">
            <div class="members-row__name">
              <span class="members-row__nick">{{ invitation.user.nickname }}</span>
              <span class="members-chip">{{ getStatusText(invitation.status) }}</span>
            </div>
            <div v-if="invitation.message" class="members-row__note">{{ invitation.message }}</div>
            <div class="members-row__note">
              {{ t('teams.members.invitedAt', { time: formatDate(invitation.createdAt) }) }}
            </div>
          </div>
          <BaseButton v-if="invitation.status === 'PENDING'" @click="emit('cancelInvitation', invitation.id)">
            {{ t('teams.members.cancelInvitation') }}
          </BaseButton>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.members-card__title {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 0;
}

.members-card__count {
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}

.members-card__loading {
  display: flex;
  justify-content: center;
  padding: 16px 24px 20px;
}

.members-card__error {
  margin: 0 24px 16px;
}

.members-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.members-row {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 60px;
  padding: 10px 16px 10px 24px;
  border-top: 1px solid var(--line);
}

.members-row:first-child {
  border-top: 0;
}

.members-row__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.members-row__name {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.members-row__nick {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 500;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.members-chip {
  flex-shrink: 0;
  padding: 0 8px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.members-row__note {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.members-row__actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}

@media (max-width: 767.98px) {
  .members-row {
    flex-wrap: wrap;
    padding: 10px 16px;
  }
}
</style>
