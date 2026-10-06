<script setup lang="ts">
// 成员与角色。名单是**管理员名单与成员表的并集**：管理员（含所有者）不一定在成员表
// 里（授管理员是往管理员关系里写一行，不是往成员表写），只读成员表会把管理员漏掉。
//
// 「加入方式」那一列：成员行带 `inviteCode`，只在核销那一刻写下。它是「有记录」的
// 证据，不是「没用过码」的证据 —— 没记录的一律显示「未知」，不拿现有的某张码顶上。
// 所有者那一行例外，他是建空间的人。
import type { SpaceAdminRoleType, SpaceMember } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { getAvatarUrl } from '@/utils/materials'

import { useSpaceData } from '@/composables/useSpaceData'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { myHandle } from '@/me'
import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

type Role = SpaceAdminRoleType | 'MEMBER'

interface Row {
  userId: number
  handle: string
  name: string
  avatarId?: number | null
  role: Role
  /** 当初用的那张码；`null` = 没有记录。 */
  viaCode: string | null
}

const { t } = useI18n()
const route = useRoute()
const dialog = useDialog()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, isOwner } = storeToRefs(spaceStore)

const spaceId = Number(route.params.spaceId)
const members = ref<SpaceMember[]>([])
const keyword = ref('')
const busy = ref(false)
// 读失败和「只有管理员、没别的成员」是两件事：失败替换掉这一块，空名单才说「暂无」。
const failed = ref(false)
const errorDetail = ref<string | null>(null)

async function refresh() {
  failed.value = false
  errorDetail.value = null
  try {
    members.value = (await SpacesApi.listMembers(spaceId)).data.members ?? []
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  }
}

onMounted(refresh)

const ORDER: Record<Role, number> = { OWNER: 0, ADMIN: 1, MEMBER: 2 }

const rows = computed<Row[]>(() => {
  const byId = new Map<number, Row>()
  for (const admin of currentSpace.value?.admins ?? []) {
    byId.set(admin.user.id, {
      userId: admin.user.id,
      handle: admin.user.username,
      name: admin.user.nickname || admin.user.username,
      avatarId: admin.user.avatarId,
      role: admin.role,
      viaCode: null,
    })
  }
  for (const m of members.value) {
    const known = byId.get(m.userId)
    const viaCode = m.inviteCode?.code ?? null
    if (known) {
      known.viaCode = viaCode
      continue
    }
    const handle = m.user?.username ?? String(m.userId)
    byId.set(m.userId, {
      userId: m.userId,
      handle,
      name: m.user?.nickname || handle,
      avatarId: m.user?.avatarId,
      role: 'MEMBER',
      viaCode,
    })
  }
  return [...byId.values()].sort((a, b) => ORDER[a.role] - ORDER[b.role])
})

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return rows.value
  return rows.value.filter((r) => r.name.toLowerCase().includes(kw) || r.handle.toLowerCase().includes(kw))
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
  codes: { name: 'SpacesDetailSettingsInviteCodes', params: { spaceId } },
  domains: { name: 'SpacesDetailSettingsDomainGroups', params: { spaceId } },
}

/** 下面三件事只有所有者能做；store 那边成功失败都会给提示。 */
async function run(action: () => Promise<void>) {
  busy.value = true
  try {
    await action()
  } catch {
    // 提示由取数那一层给
  } finally {
    busy.value = false
    await refresh()
  }
}

function makeAdmin(row: Row) {
  return run(() => spaceData.addAdmin(row.userId, 'ADMIN'))
}

/** 先问一句；取消（包括关掉对话框）就什么都不做。 */
async function confirmed(message: string, title: string): Promise<boolean> {
  try {
    return await dialog.confirm(message, { title }).wait()
  } catch {
    return false
  }
}

async function revokeAdmin(row: Row) {
  if (!(await confirmed(t('spaces.members.confirmRevoke', { name: row.name }), t('spaces.members.revoke')))) return
  await run(() => spaceData.removeAdmin(row.userId))
}

/** 转让所有者：对方成为所有者，我变成管理员。只有管理员能接手，所以成员要先设为管理员。 */
async function transferOwner(row: Row) {
  if (!(await confirmed(t('spaces.members.confirmTransfer', { name: row.name }), t('spaces.members.transfer')))) return
  await run(() => spaceData.updateAdmin(row.userId, 'OWNER'))
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
    <BaseLoadError v-if="failed" :title="t('spaces.members.loadMembersFailed')" :error="errorDetail" @retry="refresh" />
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
              <BaseButton v-if="row.role === 'MEMBER'" kind="ghost" size="sm" :disabled="busy" @click="makeAdmin(row)">
                {{ t('spaces.members.makeAdmin') }}
              </BaseButton>
              <template v-else>
                <BaseButton kind="ghost" size="sm" :disabled="busy" @click="transferOwner(row)">
                  {{ t('spaces.members.transfer') }}
                </BaseButton>
                <BaseButton kind="ghost" size="sm" :disabled="busy" @click="revokeAdmin(row)">
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
