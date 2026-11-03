<template>
  <MembersView
    :space-id="spaceId"
    :rows="rows"
    :is-owner="isOwner"
    :busy="busy"
    :failed="failed"
    :error-detail="errorDetail"
    :join-link="joinLink"
    :join-busy="joinBusy"
    :join-error="joinError"
    :join-copied="joinCopied"
    :requests="requests"
    :requests-loading="requestsLoading"
    :requests-failed="requestsFailed"
    :requests-error="requestsError"
    :answering="answering"
    :answer-error="answerError"
    @retry="refresh"
    @make-admin="makeAdmin"
    @revoke-admin="revokeAdmin"
    @transfer-owner="transferOwner"
    @reset-link="resetJoinLink"
    @set-approval="setJoinApproval"
    @copy-link="copyJoinLink"
    @approve-request="(id) => answer(id, true)"
    @reject-request="(id) => answer(id, false)"
    @retry-requests="loadRequests"
  />
</template>

<script setup lang="ts">
// 成员与角色这一页的容器：读名单（管理员名单与成员表取并集）、改角色、确认框，以及
// 加入链接（读、重置、切换「加入需要批准」、复制）和待批准的加入申请（批准、拒绝）。
// 画面在 `MembersView.vue`（场景规则见 docs/manual/dev/scenes.md）。这一页本身挂着
// `spaceManageGuard`，进得来的就是所有者或管理员，所以链接和申请进来就读。
import type { SpaceJoinLink, SpaceJoinRequest, SpaceMember } from '@/types'
import type { Role, Row } from './MembersView.vue'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import MembersView from './MembersView.vue'

import { SpacesApi } from '@/api/spaces'
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
    members.value = (await SpacesApi.listMembers(spaceId)).members ?? []
  } catch (error) {
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  }
}

onMounted(() => {
  void refresh()
  void loadJoinLink()
  void loadRequests()
})

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
    })
  }
  for (const m of members.value) {
    if (byId.has(m.userId)) continue
    const handle = m.user?.username ?? String(m.userId)
    byId.set(m.userId, {
      userId: m.userId,
      handle,
      name: m.user?.nickname || handle,
      avatarId: m.user?.avatarId,
      role: 'MEMBER',
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

// ---- 加入链接：卡只吃数据、往上报动作，取数都在这层 ----

const joinLink = ref<SpaceJoinLink | null>(null)
const joinBusy = ref(false)
const joinError = ref('')
const joinCopied = ref(false)

// 卡上的按钮和开关是一组：共用一个「在做」和一个「上一次没做成」。
async function runJoinLink(action: () => Promise<void>) {
  joinBusy.value = true
  joinError.value = ''
  try {
    await action()
  } catch {
    joinError.value = t('spaces.members.joinLink.failed')
  } finally {
    joinBusy.value = false
  }
}

function loadJoinLink() {
  return runJoinLink(async () => {
    joinLink.value = await SpacesApi.getJoinLink(spaceId)
  })
}

function resetJoinLink() {
  joinCopied.value = false
  void runJoinLink(async () => {
    joinLink.value = await SpacesApi.resetJoinLink(spaceId)
  })
}

function setJoinApproval(approval: boolean) {
  void runJoinLink(async () => {
    joinLink.value = await SpacesApi.updateJoinLink(spaceId, { approval })
  })
}

// 复制用的是浏览器那一手：不给权限、不是安全上下文就会失败，照实说一句。
async function copyJoinLink(url: string) {
  joinError.value = ''
  try {
    await navigator.clipboard.writeText(url)
    joinCopied.value = true
  } catch {
    joinError.value = t('spaces.members.joinLink.copyFailed')
  }
}

// ---- 待批准的加入申请 ----

const requests = ref<SpaceJoinRequest[]>([])
const requestsLoading = ref(false)
const requestsFailed = ref(false)
const requestsError = ref<string | null>(null)
const answering = ref<number | null>(null)
const answerError = ref('')

async function loadRequests() {
  requestsLoading.value = true
  requestsFailed.value = false
  requestsError.value = null
  try {
    requests.value = (await SpacesApi.listJoinRequests(spaceId)).requests ?? []
  } catch (error) {
    requestsFailed.value = true
    requestsError.value = error instanceof Error && error.message ? error.message : null
  } finally {
    requestsLoading.value = false
  }
}

/** 批准或拒绝一份申请；批准了的人进成员表，所以两张都重读。 */
async function answer(requestId: number, approve: boolean) {
  if (answering.value !== null) return
  answering.value = requestId
  answerError.value = ''
  try {
    if (approve) await SpacesApi.approveJoinRequest(spaceId, requestId)
    else await SpacesApi.rejectJoinRequest(spaceId, requestId)
    await Promise.all([loadRequests(), approve ? refresh() : Promise.resolve()])
  } catch {
    answerError.value = t(approve ? 'spaces.members.requests.approveFailed' : 'spaces.members.requests.rejectFailed')
  } finally {
    answering.value = null
  }
}
</script>
