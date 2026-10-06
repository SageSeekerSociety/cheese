<script setup lang="ts">
// 成员与角色这一屏的画面：页头、搜索框、那张表。读名单、改角色、确认框都归容器
// `Members.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAdminRoleType } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

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
  /** 当初用的那张码；`null` = 没有记录。 */
  viaCode: string | null
}

const props = defineProps<{
  spaceId: number
  rows: Row[]
  isOwner: boolean
  busy: boolean
  failed: boolean
  errorDetail: string | null
}>()

const emit = defineEmits<{
  retry: []
  makeAdmin: [row: Row]
  revokeAdmin: [row: Row]
  transferOwner: [row: Row]
}>()

const { t } = useI18n()

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

/** 邀请去哪：两处邀请入口都埋在设置里，页头这颗按钮把人送过去，不在这里重做一遍。 */
const inviteTargets = {
  codes: { name: 'SpacesDetailSettingsInviteCodes', params: { spaceId: props.spaceId } },
  domains: { name: 'SpacesDetailSettingsDomainGroups', params: { spaceId: props.spaceId } },
}
</script>

<template>
  <PageHeader :title="t('spaces.members.title')" show-on-mobile>
    <!-- Both ways to invite already live in the space settings. This entry points at
         them instead of re-implementing the create/invalidate flows here. -->
    <template #actions>
      <v-menu location="bottom end">
        <template #activator="{ props: activator }">
          <BaseButton v-bind="activator" kind="secondary" size="sm" prepend-icon="mdi-account-plus">
            {{ t('spaces.members.invite') }}
          </BaseButton>
        </template>
        <v-list density="compact">
          <v-list-item
            :to="inviteTargets.codes"
            prepend-icon="mdi-ticket-outline"
            :title="t('spaces.members.inviteCodes')"
          />
          <v-list-item
            :to="inviteTargets.domains"
            prepend-icon="mdi-email-outline"
            :title="t('spaces.members.inviteDomainGroups')"
          />
        </v-list>
      </v-menu>
    </template>
  </PageHeader>

  <div class="mem">
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
          <th>{{ t('spaces.members.columns.joinedVia') }}</th>
          <th class="mem__actions-head"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in filtered" :key="row.userId">
          <td>
            <div class="mem__who">
              <UserAvatar :avatar="row.avatarId ? getAvatarUrl(row.avatarId) : undefined" :name="row.name" size="28" />
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
          <td>
            <code v-if="row.viaCode" class="mem__code">{{ row.viaCode }}</code>
            <span v-else-if="row.role === 'OWNER'" class="mem__muted">{{ t('spaces.members.createdSpace') }}</span>
            <span v-else class="mem__muted">{{ t('spaces.members.unknown') }}</span>
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

.mem__code {
  padding: 2px 8px;
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.mem__actions,
.mem__actions-head {
  text-align: right;
  white-space: nowrap;
}
</style>
