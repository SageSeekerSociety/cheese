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
import type { ProjectMemberRow } from '@/cx_types'

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { addProjectMember, removeProjectMember, updateProjectMemberRole } from '@/api'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { label, PROJECT_ROLE } from '@/labels'
import { myHandle } from '@/me'
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
// 排序。它们单独一段，管理入口在 AI 队友那一页。
const people = computed(() => store.members.filter((m) => !m.agent && matches(m)))
const agents = computed(() => store.members.filter((m) => m.agent && matches(m)))

const groups = computed(() =>
  ROLES.map((role) => ({
    role,
    title: label(PROJECT_ROLE, role),
    rows: people.value.filter((m) => (m.role || 'member') === role),
  })).filter((g) => g.rows.length > 0)
)

const myRole = computed(() => store.members.find((m) => m.user_handle === me.value)?.role ?? null)
const canManage = computed(() => me.value === ownerHandle.value || myRole.value === 'lead')

// 项目所有者和自己这两行不带管理动作：把所有者降职会让项目没人管得了，而把
// 自己踢出去是一个点一下就回不来的操作，两者都不该藏在一个 ⋯ 菜单里。
function manageable(m: ProjectMemberRow): boolean {
  return canManage.value && m.user_handle !== ownerHandle.value && m.user_handle !== me.value
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
const inviteOpen = ref(false)
const inviteHandle = ref('')
const inviteRole = ref<Role>('member')
const inviting = ref(false)
async function submitInvite() {
  const handle = inviteHandle.value.trim().replace(/^@/, '')
  if (!handle) return
  inviting.value = true
  error.value = null
  try {
    await addProjectMember(props.projectId, handle, inviteRole.value)
    await store.refreshMembers()
    inviteOpen.value = false
    inviteHandle.value = ''
    inviteRole.value = 'member'
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
        v-if="store.members.length > 8"
        v-model="query"
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
            </div>
            <v-spacer />
            <v-btn
              v-if="m.user_handle !== me"
              variant="text"
              size="small"
              icon="mdi-message-outline"
              aria-label="私聊"
              title="私聊"
              @click.stop="openDm(m)"
            />
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

      <div v-if="agents.length" class="mb-6">
        <div class="t-eyebrow mb-2">AI 队友 · {{ agents.length }}</div>
        <v-card v-for="a in agents" :key="a.user_handle" class="mb-2" variant="outlined">
          <div class="d-flex align-center pa-3">
            <UserAvatar :name="a.name || a.user_handle" :avatar="faceUrl(a)" :size="36" class="mr-3" />
            <div class="min-w-0">
              <div class="t-title text-truncate">{{ a.name || a.user_handle }}</div>
              <div class="t-meta c-muted">@{{ a.user_handle }}</div>
            </div>
            <v-spacer />
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

      <div v-if="store.members.length === 0" class="text-center py-10">
        <v-icon size="34" class="mb-3 c-muted">mdi-account-group-outline</v-icon>
        <div class="t-body c-muted">还没有成员</div>
      </div>
    </v-container>

    <v-dialog v-model="inviteOpen" max-width="440">
      <v-card>
        <v-card-title class="t-title pt-4">邀请成员</v-card-title>
        <v-card-text>
          <p class="t-body c-muted mb-4">填对方的 handle（用户名）。加进来之后他能看到这个项目的全部话题</p>
          <v-text-field
            v-model="inviteHandle"
            label="handle"
            placeholder="如 zhangheng"
            prefix="@"
            density="comfortable"
            variant="outlined"
            autofocus
            @keyup.enter="submitInvite"
          />
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
          <v-btn variant="text" @click="inviteOpen = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="inviting"
            :disabled="!inviteHandle.trim()"
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
</style>
