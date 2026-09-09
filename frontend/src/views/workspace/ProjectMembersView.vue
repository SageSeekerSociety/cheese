<script setup lang="ts">
// 「成员」—— 这个项目里的人在哪里，以及对他们能做什么。
//
// 为什么值得有一整页：名册以前只作为**别的功能的下拉框**存在（改验收人、发起
// 私聊、分支保护），所以「这个项目里都有谁、谁是组长」没有任何地方回答得了，
// 而后端四条成员接口（增 / 列 / 改角色 / 移出）自始至终没有界面调得到它们。
//
// 一行 = 一个人 = 两件事：找到他（点开是他的主页，右边是私聊），和管理他（角色、
// 移出）。管理动作只对 owner / lead 出现，这条判断在后端也各做一次
// （membership/services.py），前端藏起来只是为了不给人一个必定失败的按钮。
import type { ProjectAgent, ProjectInvitation, ProjectMemberRow } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import {
  inviteProjectMember,
  listProjectAgents,
  listProjectInvitations,
  removeProjectMember,
  revokeInvitation,
  updateProjectMemberRole,
} from '@/api'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { label, PROJECT_ROLE } from '@/labels'
import { agentDmKey } from '@/lib/dm'
import { me as meRef, myHandle } from '@/me'
import { UserApi } from '@/network/api/users'
import { useWorkspaceStore } from '@/stores/workspace'

defineOptions({ name: 'ProjectMembersView' })

const props = defineProps<{ projectId: string }>()
const router = useRouter()
const store = useWorkspaceStore()

// 名册就是侧栏和 @ 补全读的那一份（store.members），不是这一页自己再拉一次的
// 副本 —— 在这里改完角色，侧栏和 @ 菜单同一刻就跟着变。
const me = computed(() => myHandle())
const project = computed(() => store.projects.find((p) => p.id === props.projectId) ?? null)
const ownerHandle = computed<string>(() => String(project.value?.owner_handle ?? ''))

// 私聊的未读全部落在这一页上：侧栏那一栏撤掉之后，「成员」那一行只说有几条，
// 是谁找你由这里的每一颗私聊按钮各自说。
function unreadWith(handle: string): number {
  return store.privateUnreadMap?.[handle] ?? 0
}

// 99 以上不再往上数：徽标的宽度会把它旁边的东西挤走，而「到底是 100 还是 137」
// 对一个「该去看看了」的信号毫无意义。侧栏那一颗用的是同一条规矩。
function countLabel(n: number): string {
  return n > 99 ? '99+' : String(n)
}

// AI 队友这一段读的是**队友列表**，不是项目名册：名册上只有平台那个共用身份
// （一行），而项目里可以有好几个队友，各有各的角色设定、模型和记忆。每个队友一
// 间私聊，所以每一行都有自己的私聊按钮和自己的未读。
// 停用的队友不列：它在已经用着它的话题里照常工作，只是不再拿出来选。
const teammates = ref<ProjectAgent[]>([])
watch(
  () => props.projectId,
  async (pid) => {
    teammates.value = []
    try {
      const rows = (await listProjectAgents(pid)).data
      if (props.projectId !== pid) return
      teammates.value = rows.filter((a) => a.is_active)
    } catch {
      // 拿不到就不显示这一段，页面其余部分照常——名册不该被一个可选接口拖垮。
    }
  },
  { immediate: true }
)

// 发出去还没被答复的邀请。它们**不在名册上**——那正是这个功能的意义：进了项目就
// 看得见全部话题，所以得由被邀请的人点头。这一段让邀请方看得见自己在等谁。
const invitations = ref<ProjectInvitation[]>([])
const revoking = ref<string | null>(null)
async function refreshInvitations() {
  const pid = props.projectId
  try {
    const payload = await listProjectInvitations(pid)
    if (props.projectId === pid) invitations.value = payload.data
  } catch {
    // 拿不到就不显示这一段，名册本身照常——它不该被一个附属列表拖垮。
  }
}
watch(() => props.projectId, refreshInvitations, { immediate: true })

async function takeBack(inv: ProjectInvitation) {
  revoking.value = inv.id
  error.value = null
  try {
    await revokeInvitation(inv.id)
    await refreshInvitations()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '撤回失败'
  } finally {
    revoking.value = null
  }
}

const query = ref('')
const busyHandle = ref<string | null>(null)
const error = ref<string | null>(null)

// 角色的显示顺序 = 组长 → 导师 → 成员。后端的枚举只有这三个（ProjectRole）。
const ROLES = ['lead', 'mentor', 'member'] as const
type Role = (typeof ROLES)[number]

function matches(m: ProjectMemberRow): boolean {
  const q = query.value.trim().toLowerCase()
  if (!q) return true
  return (m.name || '').toLowerCase().includes(q) || m.user_handle.toLowerCase().includes(q)
}

// AI 队友也在项目名册上，但它们不是「人」：没有角色可升降，也不该混在人堆里
// 排序。人这一段把它们滤掉，队友那一段（teammates）单独渲染，管理入口在 AI 队友
// 那一页。
// 名册表里存的是**除所有者以外**的人：这个仓里「谁是所有者」记在项目上
// (Project.owner_handle)，不是一行成员数据。所以他得在这里补出来——否则一个刚建
// 好的项目会对着它的主人说「还没有成员」，而他正是那个唯一确定在这儿的人。
const roster = computed<ProjectMemberRow[]>(() => {
  const rows = store.members
  if (!ownerHandle.value || rows.some((m) => m.user_handle === ownerHandle.value)) return rows
  const owner: ProjectMemberRow = { user_handle: ownerHandle.value, role: 'lead', name: ownerName.value }
  return [owner, ...rows]
})

// 所有者的显示名：名册上没有他，所以得从别处捞——是我自己就用我自己的名字，
// 否则退回 handle。写不出名字不影响这一行存在。
const ownerName = computed<string>(() =>
  ownerHandle.value === me.value ? meRef.value?.name || ownerHandle.value : ownerHandle.value
)

const people = computed(() => roster.value.filter((m) => !m.agent && matches(m)))
const agents = computed(() =>
  teammates.value.filter((a) => {
    const q = query.value.trim().toLowerCase()
    return !q || a.display_name.toLowerCase().includes(q) || a.handle.toLowerCase().includes(q)
  })
)

const groups = computed(() =>
  ROLES.map((role) => ({
    role,
    title: label(PROJECT_ROLE, role),
    rows: people.value.filter((m) => (m.role || 'member') === role),
  })).filter((g) => g.rows.length > 0)
)

const myRole = computed(() => roster.value.find((m) => m.user_handle === me.value)?.role ?? null)
const canManage = computed(() => me.value === ownerHandle.value || myRole.value === 'lead')

// 项目所有者和自己这两行不带管理动作：把所有者降职会让项目没人管得了，而把
// 自己踢出去是一个点一下就回不来的操作，两者都不该藏在一个 ⋯ 菜单里。
function manageable(m: ProjectMemberRow): boolean {
  return canManage.value && m.source !== 'team' && m.user_handle !== ownerHandle.value && m.user_handle !== me.value
}

function faceUrl(m: ProjectMemberRow): string {
  return m.avatar_id == null ? '' : getAvatarUrl(m.avatar_id)
}

function openProfile(m: ProjectMemberRow) {
  void router.push({ name: 'member', params: { projectId: props.projectId, handle: m.user_handle } })
}

// 私聊就是这一页存在的另一半：名册是「有谁」，私聊是「找他」。落点和侧栏那条
// 私聊行完全一样（同一个地址），所以从这里开的会话就是侧栏里的那一条。
function openDm(m: ProjectMemberRow) {
  void router.push({ name: 'workspace-dm', params: { projectId: props.projectId, peer: m.user_handle } })
}

// 队友的私聊地址带 `agent:` 前缀，和人的分开（见 lib/dm.ts）：队友的名字是每个
// 项目自己起的，可以跟名册上某个人撞。
function openAgentDm(a: ProjectAgent) {
  void router.push({ name: 'workspace-dm', params: { projectId: props.projectId, peer: agentDmKey(a.handle) } })
}

async function run(handle: string, fn: () => Promise<unknown>) {
  busyHandle.value = handle
  error.value = null
  try {
    await fn()
    await store.refreshMembers()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '操作失败'
  } finally {
    busyHandle.value = null
  }
}

function setRole(m: ProjectMemberRow, role: Role) {
  void run(m.user_handle, () => updateProjectMemberRole(props.projectId, m.user_handle, role))
}

// ---- 移出项目 ----
const removeTarget = ref<ProjectMemberRow | null>(null)
function confirmRemove() {
  const m = removeTarget.value
  if (!m) return
  removeTarget.value = null
  void run(m.user_handle, () => removeProjectMember(props.projectId, m.user_handle))
}

// ---- 邀请 ----
// 按 uid 邀请，而不是按 handle：uid 是个人主页地址里那个数字，找得到、抄得准；
// handle 得对方自己告诉你，而且打错一个字母的后果是「查无此人」还是「加错了人」
// 完全看运气。
//
// 所以填完先去查这个人存不存在，把查到的名字摆出来给人确认——邀请是个加人进项目
// 的动作，「我以为我加的是他」这种错必须在按下按钮之前就露出来。
const inviteOpen = ref(false)
const inviteUid = ref('')
const inviteRole = ref<Role>('member')
const inviting = ref(false)
const lookingUp = ref(false)
const foundUser = ref<{ id: number; username: string; nickname: string } | null>(null)
const lookupError = ref<string | null>(null)
let lookupTimer: ReturnType<typeof setTimeout> | null = null
let lookupSeq = 0

async function lookupUid(raw: string) {
  const uid = Number(raw.trim())
  foundUser.value = null
  lookupError.value = null
  if (!raw.trim()) return
  if (!Number.isInteger(uid) || uid < 1) {
    lookupError.value = 'uid 是一个数字，在对方个人主页的地址里'
    return
  }
  const seq = ++lookupSeq
  lookingUp.value = true
  try {
    const {
      data: { user },
    } = await UserApi.getUserInfo(uid)
    // 打字比请求快：只认最后一次发出去的那一个，否则先回来的旧结果会盖掉新的。
    if (seq !== lookupSeq) return
    if (!user) throw new Error('没有这个人')
    foundUser.value = { id: user.id, username: user.username, nickname: user.nickname }
  } catch {
    if (seq !== lookupSeq) return
    lookupError.value = `找不到 uid ${uid} 这个人`
  } finally {
    if (seq === lookupSeq) lookingUp.value = false
  }
}

watch(inviteUid, (raw) => {
  if (lookupTimer) clearTimeout(lookupTimer)
  lookupTimer = setTimeout(() => void lookupUid(raw), 350)
})

// 已经在名册上的人不能再邀请一次——后端会拒，但那是按下按钮之后才知道。
const alreadyMember = computed(
  () => !!foundUser.value && roster.value.some((m) => m.user_handle === foundUser.value?.username)
)

function resetInvite() {
  inviteOpen.value = false
  inviteUid.value = ''
  inviteRole.value = 'member'
  foundUser.value = null
  lookupError.value = null
}

async function submitInvite() {
  const user = foundUser.value
  if (!user || alreadyMember.value) return
  inviting.value = true
  error.value = null
  try {
    await inviteProjectMember(props.projectId, user.username, inviteRole.value)
    await refreshInvitations()
    resetInvite()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '邀请失败'
  } finally {
    inviting.value = false
  }
}
</script>

<template>
  <div class="members-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 900px">
      <div class="mb-4 d-flex align-center">
        <div>
          <div class="t-eyebrow mb-1">项目</div>
          <h1 class="t-page-title">成员</h1>
        </div>
        <v-spacer />
        <v-btn
          v-if="canManage"
          color="primary"
          variant="flat"
          prepend-icon="mdi-account-plus-outline"
          @click="inviteOpen = true"
        >
          邀请成员
        </v-btn>
      </div>

      <p class="t-body c-muted mb-5" style="max-width: 640px">
        {{ people.length }} 个人<span v-if="agents.length"> + {{ agents.length }} 个 AI 队友</span
        >。点一个人打开他的主页，点右边的对话图标直接私聊<span v-if="!canManage">。改角色和移出成员由组长来做</span>
      </p>

      <v-text-field
        v-if="roster.length > 8"
        v-model="query"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        clearable
        placeholder="搜名字或 handle"
        prepend-inner-icon="mdi-magnify"
        class="mb-5"
      />

      <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
        {{ error }}
      </v-alert>

      <div v-for="g in groups" :key="g.role" class="mb-6">
        <div class="t-eyebrow mb-2">{{ g.title }} · {{ g.rows.length }}</div>
        <v-card v-for="m in g.rows" :key="m.user_handle" class="mb-2 member-row" variant="outlined">
          <div class="d-flex align-center pa-3" @click="openProfile(m)">
            <UserAvatar :name="m.name || m.user_handle" :avatar="faceUrl(m)" :size="36" class="mr-3" />
            <div class="min-w-0">
              <div class="d-flex align-center ga-2">
                <span class="t-title text-truncate">{{ m.name || m.user_handle }}</span>
                <span v-if="m.user_handle === ownerHandle" class="chip-neutral">所有者</span>
                <span v-else-if="m.user_handle === me" class="chip-neutral">我</span>
              </div>
              <div class="t-meta c-muted">@{{ m.user_handle }}</div>
              <router-link v-if="m.source === 'team'" :to="`/teams/${m.team_id}`" class="t-meta"
                >来自小队 · 在小队中管理</router-link
              >
            </div>
            <v-spacer />
            <span v-if="m.user_handle !== me" class="dm-slot">
              <v-btn
                variant="text"
                size="small"
                icon="mdi-message-outline"
                aria-label="私聊"
                title="私聊"
                @click.stop="openDm(m)"
              />
              <span v-if="unreadWith(m.user_handle) > 0" class="dm-unread">
                {{ countLabel(unreadWith(m.user_handle)) }}
              </span>
            </span>
            <v-menu v-if="manageable(m)" location="bottom end">
              <template #activator="{ props: menuProps }">
                <v-btn
                  v-bind="menuProps"
                  variant="text"
                  size="small"
                  icon="mdi-dots-horizontal"
                  aria-label="管理成员"
                  :loading="busyHandle === m.user_handle"
                  @click.stop
                />
              </template>
              <v-list density="compact" nav>
                <v-list-subheader class="t-eyebrow">角色</v-list-subheader>
                <v-list-item
                  v-for="r in ROLES"
                  :key="r"
                  :active="(m.role || 'member') === r"
                  :disabled="(m.role || 'member') === r"
                  @click="setRole(m, r)"
                >
                  <v-list-item-title class="t-body">设为{{ label(PROJECT_ROLE, r) }}</v-list-item-title>
                </v-list-item>
                <v-divider class="my-1" />
                <v-list-item @click="removeTarget = m">
                  <v-list-item-title class="t-body c-danger">移出项目</v-list-item-title>
                </v-list-item>
              </v-list>
            </v-menu>
          </div>
        </v-card>
      </div>

      <div v-if="invitations.length" class="mb-6">
        <div class="t-eyebrow mb-2">等待接受 · {{ invitations.length }}</div>
        <v-card v-for="inv in invitations" :key="inv.id" class="mb-2" variant="outlined">
          <div class="d-flex align-center pa-3">
            <UserAvatar :name="inv.invitee_handle" :size="36" class="mr-3" />
            <div class="min-w-0">
              <div class="d-flex align-center ga-2">
                <span class="t-title text-truncate">@{{ inv.invitee_handle }}</span>
                <span class="chip-neutral">{{ label(PROJECT_ROLE, inv.role) }}</span>
              </div>
              <div class="t-meta c-muted">{{ inv.inviter_handle }} 邀请 · 还没答复</div>
            </div>
            <v-spacer />
            <v-btn v-if="canManage" variant="text" size="small" :loading="revoking === inv.id" @click="takeBack(inv)">
              撤回
            </v-btn>
          </div>
        </v-card>
      </div>

      <div v-if="agents.length" class="mb-6">
        <div class="t-eyebrow mb-2">AI 队友 · {{ agents.length }}</div>
        <v-card v-for="a in agents" :key="a.handle" class="mb-2 agent-row" variant="outlined">
          <div class="d-flex align-center pa-3">
            <UserAvatar :name="a.display_name || a.handle" avatar="" :size="36" class="mr-3" />
            <div class="min-w-0">
              <div class="t-title text-truncate">
                {{ a.display_name || a.handle }}
                <span v-if="a.is_default" class="chip-neutral">默认</span>
              </div>
              <div class="t-meta c-muted">@{{ a.handle }}</div>
            </div>
            <v-spacer />
            <span class="dm-slot">
              <v-btn
                variant="text"
                size="small"
                icon="mdi-message-outline"
                aria-label="私聊"
                title="私聊"
                @click.stop="openAgentDm(a)"
              />
              <span v-if="unreadWith(agentDmKey(a.handle)) > 0" class="dm-unread">
                {{ countLabel(unreadWith(agentDmKey(a.handle))) }}
              </span>
            </span>
            <v-btn
              variant="text"
              size="small"
              @click="router.push({ name: 'project-agents', params: { projectId: props.projectId } })"
            >
              设置
            </v-btn>
          </div>
        </v-card>
      </div>

      <div v-if="roster.length === 0" class="text-center py-10">
        <v-icon size="34" class="mb-3 c-muted">mdi-account-group-outline</v-icon>
        <div class="t-body c-muted">还没有成员</div>
      </div>
    </v-container>

    <v-dialog v-model="inviteOpen" max-width="440" @update:model-value="(v) => !v && resetInvite()">
      <v-card>
        <v-card-title class="t-title pt-4">邀请成员</v-card-title>
        <v-card-text>
          <p class="t-body c-muted mb-5">
            填对方的
            uid（个人主页地址里那个数字）。邀请发出去之后，要他自己接受才算加入——进来之后他能看到这个项目的全部话题
          </p>
          <v-text-field
            v-model="inviteUid"
            label="uid"
            placeholder="如 1024"
            type="number"
            inputmode="numeric"
            density="comfortable"
            variant="outlined"
            autofocus
            :loading="lookingUp"
            :error-messages="lookupError ? [lookupError] : []"
            class="mb-2"
            @keyup.enter="submitInvite"
          />
          <!-- 查到了谁，在按下按钮之前先摆出来。邀请是个把人加进项目的动作，
               「我以为我加的是他」这种错必须在这里就露出来，不能等加完了才发现。 -->
          <div v-if="foundUser" class="found-user mb-5">
            <UserAvatar :name="foundUser.nickname || foundUser.username" :size="32" class="mr-3" />
            <div class="min-w-0">
              <div class="t-body" style="font-weight: 500; color: var(--ink)">
                {{ foundUser.nickname || foundUser.username }}
              </div>
              <div class="t-meta c-muted">@{{ foundUser.username }}</div>
            </div>
            <v-spacer />
            <span v-if="alreadyMember" class="t-meta c-muted">已经在项目里</span>
          </div>
          <div v-else class="mb-5" />
          <v-select
            v-model="inviteRole"
            :items="ROLES.map((r) => ({ title: label(PROJECT_ROLE, r), value: r }))"
            label="角色"
            density="comfortable"
            variant="outlined"
            hide-details
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="resetInvite">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="inviting"
            :disabled="!foundUser || alreadyMember"
            @click="submitInvite"
          >
            邀请
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="removeTarget !== null" max-width="420" @update:model-value="removeTarget = null">
      <v-card>
        <v-card-title class="t-title pt-4"
          >把 {{ removeTarget?.name || removeTarget?.user_handle }} 移出项目？</v-card-title
        >
        <v-card-text class="t-body c-muted">
          他将看不到这个项目的话题。已经发过的消息和做过的事都留着，重新邀请可以再进来
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="removeTarget = null">取消</v-btn>
          <v-btn color="error" variant="flat" @click="confirmRemove">移出</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.members-page {
  background: var(--canvas);
}
.member-row {
  cursor: pointer;
}
.member-row:hover {
  border-color: var(--line-2);
}
.min-w-0 {
  min-width: 0;
}
/* 私聊按钮 + 它右上角那颗未读。按钮本身是 icon 按钮，徽标压在它的右上角，
   所以这个槽是定位参照系。 */
/* 查到的那个人：一行头像 + 名字，压在输入框和角色之间，所以两边都留了呼吸。 */
.found-user {
  display: flex;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
}
.dm-slot {
  position: relative;
  display: inline-flex;
}
/* 未读 = 裸的琥珀数字，没有底色。这不是随手选的样式：侧栏那颗徽标同款，而它的
   注释里写着红圆和石墨药丸都被否过。同一个产品里未读只能有一种读法，这里再造
   一颗红药丸，人就得学两遍「什么算没看」。 */
.dm-unread {
  position: absolute;
  top: -2px;
  inset-inline-end: -2px;
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  pointer-events: none;
}
</style>
