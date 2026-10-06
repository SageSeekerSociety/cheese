<template>
  <MembersView
    :space-id="spaceId"
    :rows="rows"
    :is-owner="isOwner"
    :busy="busy"
    :failed="failed"
    :error-detail="errorDetail"
    @retry="refresh"
    @make-admin="makeAdmin"
    @revoke-admin="revokeAdmin"
    @transfer-owner="transferOwner"
  />
</template>

<script setup lang="ts">
// 成员与角色这一页的容器：读名单（管理员名单与成员表取并集）、改角色、确认框。画面在
// `MembersView.vue`（场景规则见 docs/manual/dev/scenes.md）。
//
// 「加入方式」那一列：成员行带 `inviteCode`，只在核销那一刻写下。它是「有记录」的
// 证据，不是「没用过码」的证据 —— 没记录的一律显示「未知」，不拿现有的某张码顶上。
// 所有者那一行例外，他是建空间的人。
import type { SpaceMember } from '@/types'
import type { Role, Row } from './MembersView.vue'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import MembersView from './MembersView.vue'

import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const dialog = useDialog()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, isOwner } = storeToRefs(spaceStore)

const spaceId = Number(route.params.spaceId)
const members = ref<SpaceMember[]>([])
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
