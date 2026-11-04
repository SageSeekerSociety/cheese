<script setup lang="ts">
// 成员与角色这一屏的画面：页头、加入方式（加入链接与「加入需要批准」）、待批准的加入申请、
// 搜索框、那张表。读名单、读写链接、批准申请、改角色、确认框都归容器 `Members.vue`
// （场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAdminRoleType, SpaceJoinLink, SpaceJoinRequest } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import SpaceJoinLinkCard from './SpaceJoinLinkCard.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { myHandle } from '@/me'

export type Role = SpaceAdminRoleType | 'MEMBER'

export interface Row {
  userId: number
  handle: string
  name: string
  avatarId?: number | null
  role: Role
}

const props = defineProps<{
  spaceId: number
  rows: Row[]
  isOwner: boolean
  busy: boolean
  failed: boolean
  errorDetail: string | null
  /** 这个空间的加入链接；还没读回来时是 `null`。 */
  joinLink: SpaceJoinLink | null
  joinBusy: boolean
  joinError: string
  joinCopied: boolean
  /** 待批准的加入申请，最早的在前。 */
  requests: SpaceJoinRequest[]
  requestsLoading: boolean
  requestsFailed: boolean
  requestsError: string | null
  /** 正在批准或拒绝的那一份；`null` 是没有。 */
  answering: number | null
  /** 上一次批准或拒绝没成的那一句。 */
  answerError: string
}>()

const emit = defineEmits<{
  retry: []
  makeAdmin: [row: Row]
  revokeAdmin: [row: Row]
  transferOwner: [row: Row]
  resetLink: []
  setApproval: [approval: boolean]
  copyLink: [url: string]
  approveRequest: [requestId: number]
  rejectRequest: [requestId: number]
  retryRequests: []
}>()

const { t, locale } = useI18n()

const keyword = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.rows
  return props.rows.filter((r) => r.name.toLowerCase().includes(kw) || r.handle.toLowerCase().includes(kw))
})

function roleLabel(role: Role): string {
  return t(`spaces.members.role.${role.toLowerCase()}`)
}

/** 角色旁边那一句「这个角色能做什么」。裸标签说不出成员与管理员的差别，这里补上。 */
function roleHint(role: Role): string {
  if (role === 'OWNER') return t('spaces.members.role.ownerHint')
  if (role === 'ADMIN') return t('spaces.members.role.adminHint')
  return t('spaces.members.role.memberHint')
}

/** 邮箱域名组在设置里：页头这颗按钮把人送过去，不在这里重做一遍。 */
const domainGroupsTarget = { name: 'SpacesDetailSettingsDomainGroups', params: { spaceId: props.spaceId } }

// 申请那一块：开着「加入需要批准」时总在；关着时只在还有没处理的申请（或读失败）时出现。
const showRequests = computed(() => !!props.joinLink?.approval || props.requests.length > 0 || props.requestsFailed)

function requesterName(request: SpaceJoinRequest): string {
  return request.user?.nickname || request.user?.username || String(request.userId)
}

function formatTime(at: number): string {
  return new Date(at).toLocaleString(locale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <PageHeader :title="t('spaces.members.title')" show-on-mobile>
    <!-- Email domain groups live in the space settings; this entry points there. -->
    <template #actions>
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-email-outline" :to="domainGroupsTarget">
        {{ t('spaces.members.inviteDomainGroups') }}
      </BaseButton>
    </template>
  </PageHeader>

  <div class="mem">
    <SpaceJoinLinkCard
      class="mem__card"
      :link="joinLink"
      :busy="joinBusy"
      :error="joinError"
      :copied="joinCopied"
      @reset="emit('resetLink')"
      @set-approval="(approval) => emit('setApproval', approval)"
      @copy="(url) => emit('copyLink', url)"
    />

    <section v-if="showRequests" class="settings-card mem__card">
      <h2 class="settings-card__title mem__card-title">{{ t('spaces.members.requests.title') }}</h2>
      <p v-if="answerError" role="alert" class="t-body mem__error">{{ answerError }}</p>
      <BaseLoadError
        v-if="requestsFailed"
        class="mem__requests-error"
        :title="t('spaces.members.requests.loadFailed')"
        :error="requestsError"
        @retry="emit('retryRequests')"
      />
      <div v-else-if="requestsLoading && !requests.length" class="mem__requests-loading">
        <v-progress-linear indeterminate :aria-label="t('spaces.members.joinLink.loading')" />
      </div>
      <ul v-else-if="requests.length" class="mem__requests">
        <li v-for="request in requests" :key="request.id" class="mem__request">
          <UserAvatar
            :avatar="getAvatarUrl(request.user?.avatarId)"
            :name="requesterName(request)"
            :seed="request.user?.username ?? String(request.userId)"
            size="28"
          />
          <div class="mem__request-main">
            <div class="mem__name">{{ requesterName(request) }}</div>
            <div class="mem__muted">
              {{ t('spaces.members.requests.requestedAt', { time: formatTime(request.createdAt) }) }}
            </div>
          </div>
          <div class="mem__request-actions">
            <BaseButton
              kind="ghost"
              size="sm"
              :disabled="answering !== null"
              @click="emit('rejectRequest', request.id)"
            >
              {{ t('spaces.members.requests.reject') }}
            </BaseButton>
            <BaseButton
              kind="secondary"
              size="sm"
              :loading="answering === request.id"
              :disabled="answering !== null"
              @click="emit('approveRequest', request.id)"
            >
              {{ t('spaces.members.requests.approve') }}
            </BaseButton>
          </div>
        </li>
      </ul>
      <p v-else class="mem__muted mem__requests-empty">{{ t('spaces.members.requests.empty') }}</p>
    </section>

    <BaseLoadError
      v-if="failed"
      :title="t('spaces.members.loadMembersFailed')"
      :error="errorDetail"
      @retry="emit('retry')"
    />
    <div v-else class="mem__bar">
      <v-text-field
        v-model="keyword"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        prepend-inner-icon="mdi-magnify"
        :placeholder="t('spaces.members.search')"
        :aria-label="t('spaces.members.search')"
        class="mem__search"
      />
      <span class="mem__count t-num">{{ t('spaces.members.count', { n: rows.length }) }}</span>
    </div>
    <v-table v-if="!failed && filtered.length" density="comfortable" class="mem__table">
      <thead>
        <tr>
          <th>{{ t('spaces.members.columns.member') }}</th>
          <th>{{ t('spaces.members.columns.role') }}</th>
          <th class="mem__actions-head"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in filtered" :key="row.userId">
          <td>
            <div class="mem__who">
              <!-- getAvatarUrl 对 null / undefined / 0 一律回空串（不再回 /avatars/default），
                   所以这里直接交给它就行，不用再套一层真值判断；没头像时 UserAvatar 自己
                   走彩色首字母。seed 传 handle，颜色跟着人走、不跟昵称走。 -->
              <UserAvatar :avatar="getAvatarUrl(row.avatarId)" :name="row.name" :seed="row.handle" size="28" />
              <div>
                <div class="mem__name">
                  {{ row.name }}
                  <span v-if="row.handle === myHandle()" class="mem__muted">{{ t('spaces.members.you') }}</span>
                </div>
                <div class="mem__muted">{{ row.handle }}</div>
              </div>
            </div>
          </td>
          <td>
            <div>{{ roleLabel(row.role) }}</div>
            <div class="mem__muted">{{ roleHint(row.role) }}</div>
          </td>
          <td class="mem__actions">
            <template v-if="isOwner && row.role !== 'OWNER'">
              <BaseButton
                v-if="row.role === 'MEMBER'"
                kind="ghost"
                size="sm"
                :disabled="busy"
                @click="emit('makeAdmin', row)"
              >
                {{ t('spaces.members.makeAdmin') }}
              </BaseButton>
              <template v-else>
                <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('transferOwner', row)">
                  {{ t('spaces.members.transfer') }}
                </BaseButton>
                <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('revokeAdmin', row)">
                  {{ t('spaces.members.revoke') }}
                </BaseButton>
              </template>
            </template>
          </td>
        </tr>
      </tbody>
    </v-table>
    <p v-else-if="!failed" class="mem__muted">{{ t('spaces.members.empty') }}</p>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.mem {
  /* 宽屏下封顶居中（和项目里的页面一样），不再左贴、右边空一条。 */
  max-width: 960px;
  margin-inline: auto;
  padding: 16px 16px 48px;
}

.mem__bar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}

.mem__search {
  flex: 0 1 280px;
}

.mem__count {
  margin-left: auto;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.mem__table {
  background: transparent;
}

.mem__table th {
  white-space: nowrap;
}

.mem__who {
  display: flex;
  gap: 10px;
  align-items: center;
}

.mem__name {
  color: var(--ink);
  font-size: 14px;
}

.mem__muted {
  color: var(--muted);
  font-size: 13px;
}

.mem__card {
  margin-bottom: 16px;
}

.mem__card-title {
  margin: 0;
}

.mem__error {
  padding: 0 24px 8px;
  margin: 0;
  color: var(--danger-ink);
}

.mem__requests-error {
  margin: 0 24px 16px;
}

.mem__requests-loading,
.mem__requests-empty {
  padding: 8px 24px 16px;
  margin: 0;
}

.mem__requests {
  margin: 0;
  padding: 0;
  list-style: none;
}

.mem__request {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 16px 10px 24px;
  border-top: 1px solid var(--line);
}

.mem__request-main {
  flex: 1;
  min-width: 0;
}

.mem__request-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}

@media (max-width: 767.98px) {
  .mem__request {
    flex-wrap: wrap;
    padding: 10px 16px;
  }
}

.mem__actions,
.mem__actions-head {
  text-align: right;
  white-space: nowrap;
}
</style>
