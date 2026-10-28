<script setup lang="ts">
// 「成员」—— 这个项目里有谁、各自是什么角色，以及对他们能做什么（#3830）。
//
// 按角色分段：所有者、管理员、成员、导师，AI 队友在最后。团队所有者和团队管理员带标签：
// 他们的身份是团队给的，只能在团队那边改。外部成员（团队以外、被邀请进来的人）带「外部」。
// 我能做什么由项目上的 `my_role` / `my_permissions` 说了算，后端动手时按同一条规则再判
// 一次；它们只在 `GET /projects/{id}` 上有，所以这一页自己问一次。
//
// 一行 = 一个人：点开是他的主页，右边是私聊；比我低的人还有一个「⋯」——改角色、移出。
// 只往下管：管理员管成员和导师，设管理员、撤管理员只有所有者能做。
//
// 右上角：邀请（选成员还是导师）、角色和权限、所有者的「转让项目」（个人项目还有「移到
// 团队」）、其余人的「退出项目」。团队成员退的也是**这个项目**：他还在团队里。
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectAgent, ProjectInvitation, ProjectMemberRow } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'

import { getAvatarUrl } from '@/utils/materials'

import { useAccountLookup } from '@/composables/useAccountLookup'
import { useMoveProject } from '@/composables/useMoveProject'
import { provideRevealGate } from '@/composables/useRevealGate'
import { useRowMenu } from '@/composables/useRowMenu'

import {
  ApiError,
  cancelTransfer,
  inviteExternalMember,
  listProjectInvitations,
  removeProjectMember,
  revokeInvitation,
  setMemberRole,
} from '@/api'
import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import ExternalTag from '@/components/common/ExternalTag.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import LeaveProjectDialog from '@/components/LeaveProjectDialog.vue'
import MoveProjectDialog from '@/components/MoveProjectDialog.vue'
import TransferProjectDialog from '@/components/TransferProjectDialog.vue'
import { t } from '@/i18n'
import { memberName, memberOriginalName, teammateName } from '@/lib/agentNames'
import { agentDmKey } from '@/lib/dm'
import { isExternalMember } from '@/lib/externalMembers'
import { mayIn } from '@/lib/projectPermissions'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { agentsQuery, projectQuery } from '@/query/project'
import { useWorkspaceStore } from '@/stores/workspace'

defineOptions({ name: 'ProjectMembersView' })

const props = defineProps<{ projectId: string }>()
const router = useRouter()
const store = useWorkspaceStore()
const rowMenu = useRowMenu<string>()
const gate = provideRevealGate()
const { revealed } = gate

// 名册由 store 去取；一个项目至少有它的所有者，名册非空就是到货了。
const releaseRoster = gate.hold()
watch(
  () => store.members.length,
  (n) => n > 0 && releaseRoster(),
  { immediate: true }
)

// 名册就是侧栏和 @ 补全读的那一份（store.members），不是这一页自己再拉一次的副本。
const me = computed(() => myHandle())
const project = computed(() => store.projects.find((p) => p.id === props.projectId) ?? null)
const ownerHandle = computed<string>(() => String(project.value?.owner_handle ?? ''))
// 我管不管这个项目的成员：和工作区别处读同一份项目（`query/project`）。
const projectRead = useQuery(
  computed(() => projectQuery(props.projectId)),
  queryClient
)
const myRole = computed(() => projectRead.data.value?.my_role ?? null)
const canManage = computed(() => mayIn(projectRead.data.value, 'manage_members'))
const changesRoles = computed(() => mayIn(projectRead.data.value, 'change_roles'))

function unreadWith(handle: string): number {
  return store.privateUnreadMap?.[handle] ?? 0
}

// 99 以上不再往上数：徽标的宽度会把旁边的东西挤走。侧栏那一颗用的是同一条规矩。
function countLabel(n: number): string {
  return n > 99 ? '99+' : String(n)
}

// AI 队友这一段读的是队友列表，不是名册上那几行：私聊地址、未读键和「默认」那颗标
// 问的都是队友本身，名册行给不出来。停用的队友不列。
// 拿不到就不显示这一段，名册不该被一个可选接口拖垮。
const agentsRead = useQuery(
  computed(() => agentsQuery(props.projectId)),
  queryClient
)
const teammates = computed<ProjectAgent[]>(() => (agentsRead.data.value ?? []).filter((a) => a.is_active))
const releaseTeammates = gate.hold()
watch(
  () => agentsRead.isPending.value,
  (pending) => {
    if (!pending) releaseTeammates()
  },
  { immediate: true }
)

// 发出去还没被答复的邀请：这些人**还不在名册上**，要他们自己点头才进来。
// The list endpoint also returns who the invitee is (`InvitationService.describe`).
// Declared here, not on `ProjectInvitation`: cx_types.ts is over its size cap and may only shrink.
type PendingInvitation = ProjectInvitation & { invitee_name?: string; invitee_avatar_id?: number | null }
const invitations = ref<PendingInvitation[]>([])
const revoking = ref<string | null>(null)
async function refreshInvitations() {
  const pid = props.projectId
  try {
    const payload = await listProjectInvitations(pid)
    if (props.projectId === pid) invitations.value = payload.data as PendingInvitation[]
  } catch {
    // 同上：拿不到就不显示这一段。
  }
}
const releaseInvitations = gate.hold()
watch(
  () => props.projectId,
  () => refreshInvitations().finally(releaseInvitations),
  { immediate: true }
)

const query = ref('')
const busyHandle = ref<string | null>(null)
const error = ref<string | null>(null)

function matches(m: ProjectMemberRow): boolean {
  const q = query.value.trim().toLowerCase()
  if (!q) return true
  return (
    memberName(m).toLowerCase().includes(q) ||
    memberOriginalName(m).toLowerCase().includes(q) ||
    m.user_handle.toLowerCase().includes(q)
  )
}

const people = computed(() => store.members.filter((m) => !m.agent && matches(m)))
const agents = computed(() =>
  teammates.value.filter((a) => {
    const q = query.value.trim().toLowerCase()
    return (
      !q || teammateName(a.display_name, a.name_source).toLowerCase().includes(q) || a.handle.toLowerCase().includes(q)
    )
  })
)

// 名册行上的角色（#3830）：后端说这个人坐在哪（`role`），以及这个身份是不是团队给的
// （`role_from_team`，团队所有者和团队管理员，只能在团队那边改）。
// Declared here, not on `ProjectMemberRow`: cx_types.ts is over its size cap and may only shrink.
type Seat = 'owner' | 'admin' | 'member' | 'mentor'
type RoleRow = ProjectMemberRow & { role?: Seat; role_from_team?: boolean }
const RANK: Record<Seat, number> = { owner: 3, admin: 2, member: 1, mentor: 1 }
function roleOf(m: ProjectMemberRow): Seat {
  return (m as RoleRow).role ?? 'member'
}
function teamChip(m: ProjectMemberRow): string | null {
  const row = m as RoleRow
  if (!row.role_from_team) return null
  return t(row.role === 'owner' ? 'work.members.teamOwner' : 'work.members.teamAdmin')
}

// 按角色分段。
const sections = computed(() =>
  (['owner', 'admin', 'member', 'mentor'] as const)
    .map((role) => ({
      key: role,
      title: t(`work.members.section${role[0].toUpperCase()}${role.slice(1)}`),
      rows: people.value.filter((m) => roleOf(m) === role),
    }))
    .filter((s) => s.rows.length > 0)
)

// 只往下管（#3830）：比自己低的人才有这一行的菜单。团队给的身份（团队所有者、团队管理员）
// 在团队那边改，这里不给菜单（后端按同一条规则再判一次）。自己走右上角那颗「退出项目」。
function below(m: ProjectMemberRow): boolean {
  const mine = myRole.value
  if (!mine || m.user_handle === me.value || (m as RoleRow).role_from_team) return false
  return RANK[mine] > RANK[roleOf(m)]
}
function removable(m: ProjectMemberRow): boolean {
  return below(m) && (canManage.value || changesRoles.value)
}

function faceUrl(m: ProjectMemberRow): string {
  return m.avatar_id == null ? '' : getAvatarUrl(m.avatar_id)
}

function openProfile(m: ProjectMemberRow) {
  void router.push({ name: 'member', params: { projectId: props.projectId, handle: m.user_handle } })
}

function openDm(m: ProjectMemberRow) {
  void router.push({ name: 'workspace-dm', params: { projectId: props.projectId, peer: m.user_handle } })
}

function openAgentDm(a: ProjectAgent) {
  void router.push({ name: 'workspace-dm', params: { projectId: props.projectId, peer: agentDmKey(a.handle) } })
}

/** AI 队友也有资料页。它那一格只回答「带了哪几颗」，是只读的；改在项目设置里（`AgentTeamSettings`）。
 *  地址用**座位名**（`seat_handle`）——资料页拿它跟 `/projects/{id}/agents` 对得上（见 `ProfileView`）。 */
function openAgentProfile(a: ProjectAgent) {
  void router.push({ name: 'member', params: { projectId: props.projectId, handle: a.seat_handle } })
}

function messageOf(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback
}

// ---- 改角色、移出 ----
const removeTarget = ref<ProjectMemberRow | null>(null)
function memberActions(m: ProjectMemberRow): MenuAction[] {
  const role = roleOf(m)
  const make = (key: 'admin' | 'member' | 'mentor', icon: string): MenuAction => ({
    key,
    label: t(`work.members.make${key[0].toUpperCase()}${key.slice(1)}`),
    icon,
    onSelect: () => void changeRole(m, key),
  })
  const roles: MenuAction[] = []
  if (changesRoles.value) {
    // 设管理员、撤管理员都只有所有者能做。
    if (role === 'member' && myRole.value === 'owner') roles.push(make('admin', 'mdi-shield-account-outline'))
    if (role === 'member') roles.push(make('mentor', 'mdi-school-outline'))
    if (role === 'mentor' || role === 'admin') roles.push(make('member', 'mdi-account-outline'))
  }
  return [
    ...roles,
    ...(canManage.value
      ? [
          {
            key: 'remove',
            label: t('work.members.remove'),
            icon: 'mdi-account-remove-outline',
            danger: true,
            onSelect: () => (removeTarget.value = m),
          },
        ]
      : []),
  ]
}
async function changeRole(m: ProjectMemberRow, role: 'admin' | 'member' | 'mentor') {
  busyHandle.value = m.user_handle
  error.value = null
  try {
    await setMemberRole(props.projectId, m.user_handle, role)
    await store.refreshMembers()
  } catch (e) {
    error.value = messageOf(e, t('work.members.roleFailed'))
  } finally {
    busyHandle.value = null
  }
}
async function confirmRemove() {
  const m = removeTarget.value
  if (!m) return
  removeTarget.value = null
  busyHandle.value = m.user_handle
  error.value = null
  try {
    await removeProjectMember(props.projectId, m.user_handle)
    await store.refreshMembers()
  } catch (e) {
    error.value = messageOf(e, t('work.members.failed'))
  } finally {
    busyHandle.value = null
  }
}

async function takeBack(inv: ProjectInvitation) {
  revoking.value = inv.id
  error.value = null
  try {
    await revokeInvitation(inv.id)
    await refreshInvitations()
  } catch (e) {
    error.value = messageOf(e, t('work.members.revokeFailed'))
  } finally {
    revoking.value = null
  }
}

// ---- 退出项目 / 转让项目 ----
// 「退出」给名册上的每一个人，只除所有者：他一走项目就没人管，换一颗「转让项目」。
// 团队成员也在这颗按钮底下——他退的是**这个项目**，不是小队（后端为它记下一条项目级
// 事实，小队那一行不动）。名册行还没到时不给「退出」——不知道他是不是在项目里，就别
// 递一颗可能必然失败的按钮（他刚在这里退过一次也是这种情况：名册上已经没有他了）。
const leaveOpen = ref(false)
const transferOpen = ref(false)
const isOwner = computed(() => myRole.value === 'owner')
const canLeave = computed(() => {
  const row = store.members.find((m) => m.user_handle === me.value)
  return !!row && !isOwner.value
})

// 发出去还没被接受的转让：所有者看得到在等谁，可以撤回。
const pendingTransfer = computed(() => projectRead.data.value?.pending_transfer ?? null)
const pendingName = computed(() => {
  const handle = pendingTransfer.value?.to_handle
  const row = store.members.find((m) => m.user_handle === handle)
  return row ? memberName(row) || row.user_handle : handle ?? ''
})
const withdrawing = ref(false)
async function withdrawTransfer() {
  withdrawing.value = true
  error.value = null
  try {
    await cancelTransfer(props.projectId)
    await queryClient.invalidateQueries({ queryKey: projectQuery(props.projectId).queryKey })
  } catch (e) {
    error.value = messageOf(e, t('work.members.failed'))
  } finally {
    withdrawing.value = false
  }
}

// ---- 移到团队：所有者把自己的个人项目移到自己管理的团队 ----
const personal = computed(() => !!project.value?.team_handle && project.value.team_handle === ownerHandle.value)
const moveOpen = ref(false)
const moving = useMoveProject(() => props.projectId)
watch(moveOpen, (open) => {
  if (open) void moving.load()
})
async function moveTo(teamId: number) {
  if (await moving.move(teamId)) moveOpen.value = false
}

// ---- 邀请外部成员 ----
// 像飞书加外部联系人：填完整的用户名或邮箱，先把查到的人摆出来确认，再发邀请。只认
// 精确匹配——邀请是把人放进项目的动作，「我以为我请的是他」这种错必须在按下按钮之
// 前就露出来。
const inviteOpen = ref(false)
const inviting = ref(false)
const inviteRole = ref<'member' | 'mentor'>('member')
const inviteRoleOptions = computed(() => [
  { value: 'member', label: t('work.projectSettings.roles.role.member') },
  { value: 'mentor', label: t('work.projectSettings.roles.role.mentor') },
])
const {
  query: inviteQuery,
  found,
  lookingUp,
  lookupError,
} = useAccountLookup((e) =>
  e instanceof ApiError && e.status === 404 ? t('work.members.notFound') : messageOf(e, t('work.members.failed'))
)

// 已经在项目里的人（团队成员、所有者、已有的外部成员）不用再邀请——后端也会拒，但那
// 是按下按钮之后才知道。
const alreadyIn = computed(() => !!found.value && store.members.some((m) => m.user_handle === found.value?.handle))

function resetInvite() {
  inviteOpen.value = false
  inviteQuery.value = ''
  found.value = null
  lookupError.value = null
}

async function submitInvite() {
  const user = found.value
  if (!user || alreadyIn.value) return
  inviting.value = true
  error.value = null
  try {
    await inviteExternalMember(props.projectId, user.handle, inviteRole.value)
    await refreshInvitations()
    resetInvite()
  } catch (e) {
    lookupError.value = messageOf(e, t('work.members.inviteFailed'))
  } finally {
    inviting.value = false
  }
}
useCommands(() => [
  ...(canLeave.value
    ? [
        {
          id: 'members.leave',
          title: t('work.members.leave'),
          icon: 'mdi-exit-to-app',
          danger: true,
          header: {},
          run: () => (leaveOpen.value = true),
        },
      ]
    : []),
  ...(isOwner.value
    ? [
        {
          id: 'members.transfer',
          title: t('work.members.transfer'),
          icon: 'mdi-account-arrow-right-outline',
          header: {},
          run: () => (transferOpen.value = true),
        },
      ]
    : []),
  ...(isOwner.value && personal.value
    ? [
        {
          id: 'members.move',
          title: t('work.members.moveToTeam'),
          icon: 'mdi-account-group-outline',
          header: {},
          run: () => (moveOpen.value = true),
        },
      ]
    : []),
  {
    id: 'members.roles',
    title: t('work.members.roles'),
    icon: 'mdi-shield-account-outline',
    header: {},
    run: () => void router.push({ name: 'project-settings', params: { projectId: props.projectId, section: 'roles' } }),
  },
  ...(canManage.value
    ? [
        {
          id: 'members.invite',
          title: t('work.members.invite'),
          icon: 'mdi-account-plus-outline',
          header: { primary: true, accent: true },
          run: () => (inviteOpen.value = true),
        },
      ]
    : []),
])
</script>

<template>
  <AppPage :title="t('navigation.project.members')">
    <!-- 名册、队友、邀请三处各自到货；到齐之前整页藏在转圈后面，不然后到的名册会
         把先画出来的队友那一段往下推一整屏。见 useRevealGate。 -->
    <div class="reveal-gate" :class="{ 'reveal-gate--waiting': !revealed }">
      <v-text-field
        v-if="store.members.length > 8"
        v-model="query"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        clearable
        :placeholder="t('work.members.search')"
        prepend-inner-icon="mdi-magnify"
        class="mb-5"
      />

      <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
        {{ error }}
      </v-alert>

      <div v-for="s in sections" :key="s.key" class="mb-6" :data-section="s.key">
        <div class="t-eyebrow mb-2 section-head">
          <span>{{ s.title }}</span
          ><span class="c-muted">{{ s.rows.length }}</span>
        </div>
        <p v-if="s.key === 'admin'" class="t-meta-read mb-2">{{ t('work.members.adminHint') }}</p>
        <div v-if="s.key === 'owner' && pendingTransfer" class="pending-transfer mb-2" data-testid="pending-transfer">
          <span class="t-meta c-muted">{{ t('work.members.pendingTransfer', { name: pendingName }) }}</span>
          <BaseButton kind="ghost" size="sm" :loading="withdrawing" @click="withdrawTransfer">
            {{ t('work.members.cancelTransfer') }}
          </BaseButton>
        </div>
        <v-card
          v-for="m in s.rows"
          :key="m.user_handle"
          class="mb-2 member-row"
          variant="outlined"
          @contextmenu="removable(m) && rowMenu.open(m.user_handle, $event)"
        >
          <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- a click anywhere on the row opens the profile; the name button does the same for the keyboard -->
          <div class="d-flex align-center pa-3" @click="openProfile(m)">
            <UserAvatar :name="memberName(m) || m.user_handle" :avatar="faceUrl(m)" :size="36" class="mr-3" />
            <div class="min-w-0">
              <div class="d-flex align-center ga-2">
                <button type="button" class="t-title text-truncate member-name" @click.stop="openProfile(m)">
                  {{ memberName(m) || m.user_handle }}
                </button>
                <span
                  v-if="m.remark && memberOriginalName(m) && memberOriginalName(m) !== m.remark"
                  class="t-meta c-muted text-truncate"
                  >({{ memberOriginalName(m) }})</span
                >
                <ExternalTag v-if="isExternalMember(m)" />
                <span v-if="teamChip(m)" class="chip-neutral">{{ teamChip(m) }}</span>
                <span v-if="m.user_handle === me" class="chip-neutral">{{ t('work.members.me') }}</span>
              </div>
              <div class="t-meta c-muted">{{ m.user_handle }}</div>
              <router-link
                v-if="!isExternalMember(m) && m.team_handle"
                :to="{ name: 'TeamsDetailDefault', params: { handle: m.team_handle } }"
                class="t-meta-read member-team-link"
                :data-user-content="m.team_name || undefined"
                @click.stop
                >{{ t('work.members.fromTeam', { name: m.team_name || m.team_handle }) }}</router-link
              >
            </div>
            <v-spacer />
            <span v-if="m.user_handle !== me" class="dm-slot">
              <BaseButton
                size="sm"
                icon="mdi-message-outline"
                :aria-label="t('work.members.dm')"
                :title="t('work.members.dm')"
                @click.stop="openDm(m)"
              />
              <span v-if="unreadWith(m.user_handle) > 0" class="dm-unread">
                {{ countLabel(unreadWith(m.user_handle)) }}
              </span>
            </span>
            <!-- 桌面是下拉菜单，手机是底部面板（AdaptiveMenu）。 -->
            <AdaptiveMenu
              v-if="removable(m)"
              v-bind="rowMenu.bind(m.user_handle)"
              :actions="memberActions(m)"
              :title="memberName(m) || m.user_handle"
            >
              <template #activator="{ props: menuProps }">
                <BaseButton
                  v-bind="menuProps"
                  size="sm"
                  icon="mdi-dots-horizontal"
                  class="tap-target"
                  :aria-label="t('work.members.manage')"
                  :loading="busyHandle === m.user_handle"
                  @click.stop
                />
              </template>
            </AdaptiveMenu>
          </div>
        </v-card>
      </div>

      <div v-if="invitations.length" class="mb-6" data-section="pending">
        <div class="t-eyebrow mb-2 section-head">
          <span>{{ t('work.members.sectionPending') }}</span
          ><span class="c-muted">{{ invitations.length }}</span>
        </div>
        <v-card v-for="inv in invitations" :key="inv.id" class="mb-2" variant="outlined">
          <div class="d-flex align-center pa-3">
            <UserAvatar
              :name="inv.invitee_name || inv.invitee_handle"
              :avatar="inv.invitee_avatar_id == null ? '' : getAvatarUrl(inv.invitee_avatar_id)"
              :size="36"
              class="mr-3"
            />
            <div class="min-w-0">
              <div class="d-flex align-center ga-2">
                <span class="t-title text-truncate">{{ inv.invitee_name || inv.invitee_handle }}</span>
                <ExternalTag />
              </div>
              <div v-if="inv.invitee_name" class="t-meta c-muted">{{ inv.invitee_handle }}</div>
              <i18n-t keypath="work.members.pendingBy" tag="div" class="t-meta c-muted">
                <template #inviter>
                  <i18n-t v-if="inv.invited_by_assistant" keypath="work.onBehalf.who" tag="span">
                    <template #name><UserRef :handle="inv.inviter_handle" /></template>
                  </i18n-t>
                  <UserRef v-else :handle="inv.inviter_handle" />
                </template>
              </i18n-t>
            </div>
            <v-spacer />
            <BaseButton v-if="canManage" kind="ghost" size="sm" :loading="revoking === inv.id" @click="takeBack(inv)">
              {{ t('work.members.revoke') }}
            </BaseButton>
          </div>
        </v-card>
      </div>

      <div v-if="agents.length" class="mb-6">
        <div class="t-eyebrow mb-2 section-head">
          <span>{{ t('work.members.sectionAgents') }}</span
          ><span class="c-muted">{{ agents.length }}</span>
        </div>
        <v-card v-for="a in agents" :key="a.handle" class="mb-2 agent-row" variant="outlined">
          <div
            class="d-flex align-center pa-3"
            role="button"
            tabindex="0"
            @click="openAgentProfile(a)"
            @keydown.enter="openAgentProfile(a)"
            @keydown.space.prevent="openAgentProfile(a)"
          >
            <CheeseAvatar
              :name="teammateName(a.display_name, a.name_source) || a.handle"
              :handle="a.seat_handle"
              :size="36"
              class="mr-3"
            />
            <div class="min-w-0">
              <div class="t-title text-truncate">
                {{ teammateName(a.display_name, a.name_source) || a.handle }}
                <span v-if="a.is_default" class="chip-neutral">{{ t('work.members.agentDefault') }}</span>
              </div>
              <div class="t-meta c-muted">{{ a.handle }}</div>
            </div>
            <v-spacer />
            <span class="dm-slot">
              <BaseButton
                size="sm"
                icon="mdi-message-outline"
                :aria-label="t('work.members.dm')"
                :title="t('work.members.dm')"
                @click.stop="openAgentDm(a)"
              />
              <span v-if="unreadWith(agentDmKey(a.handle)) > 0" class="dm-unread">
                {{ countLabel(unreadWith(agentDmKey(a.handle))) }}
              </span>
            </span>
            <BaseButton
              size="sm"
              @click.stop="
                router.push({ name: 'project-settings', params: { projectId: props.projectId, section: 'agents' } })
              "
            >
              {{ t('work.members.agentSettings') }}
            </BaseButton>
          </div>
        </v-card>
      </div>

      <div v-if="store.members.length === 0" class="text-center py-10">
        <v-icon size="34" class="mb-3 c-muted">mdi-account-group-outline</v-icon>
        <div class="t-body c-muted">{{ t('work.members.empty') }}</div>
      </div>
      <div v-if="!revealed" class="reveal-gate__wait">
        <v-progress-circular indeterminate color="primary" />
      </div>
    </div>

    <!-- 邀请要打字：手机上是整页，发送在页头右边，键盘弹起来也够得着。 -->
    <AdaptiveDialog
      v-model="inviteOpen"
      :title="t('work.members.inviteTitle')"
      :primary-label="t('work.members.send')"
      :primary-loading="inviting"
      :primary-disabled="!found || alreadyIn"
      :cancel-label="t('work.members.cancel')"
      :max-width="440"
      @update:model-value="(v: boolean) => !v && resetInvite()"
      @primary="submitInvite"
    >
      <p class="t-body c-muted mb-5">{{ t('work.members.inviteHint') }}</p>
      <BaseField
        :label="t('work.members.inviteRole')"
        :hint="t(`work.members.inviteRoleHint.${inviteRole}`)"
        class="mb-5"
      >
        <SegmentedControl
          v-model="inviteRole"
          :options="inviteRoleOptions"
          :label="t('work.members.inviteRole')"
          size="md"
        />
      </BaseField>
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- the person opened this dialog to look someone up; focus moves into the search field -->
      <v-text-field
        v-model="inviteQuery"
        autocomplete="off"
        :label="t('work.members.inviteLabel')"
        :placeholder="t('work.members.invitePlaceholder')"
        density="comfortable"
        variant="outlined"
        autofocus
        :loading="lookingUp"
        :error-messages="lookupError ? [lookupError] : []"
        class="mb-2"
        @keyup.enter="submitInvite"
      />
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
      <div v-if="found" class="found-user mb-2" data-testid="found-user">
        <UserAvatar
          :name="found.name || found.handle"
          :avatar="found.avatar_id == null ? '' : getAvatarUrl(found.avatar_id)"
          :size="32"
          class="mr-3"
        />
        <div class="min-w-0">
          <div class="t-body found-user__name">{{ found.name || found.handle }}</div>
          <div class="t-meta c-muted">{{ found.handle }}</div>
        </div>
        <v-spacer />
        <span v-if="alreadyIn" class="t-meta c-muted">{{ t('work.members.alreadyIn') }}</span>
      </div>
    </AdaptiveDialog>

    <LeaveProjectDialog v-model="leaveOpen" :project-id="props.projectId" />
    <TransferProjectDialog v-model="transferOpen" :project-id="props.projectId" />
    <MoveProjectDialog
      v-model="moveOpen"
      :teams="moving.teams.value"
      :loading="moving.loading.value"
      :moving="moving.moving.value"
      :error="moving.error.value"
      @move="moveTo"
    />

    <ConfirmDialog
      :model-value="removeTarget !== null"
      :title="t('work.members.removeTitle', { name: memberName(removeTarget) || removeTarget?.user_handle || '' })"
      :confirm-label="t('work.members.confirmRemove')"
      danger
      @update:model-value="removeTarget = null"
      @confirm="confirmRemove"
    >
      {{ t('work.members.removeBody') }}
    </ConfirmDialog>
  </AppPage>
</template>

<style scoped>
.member-row,
.agent-row {
  cursor: pointer;
}

/* The name is a plain-text button (the keyboard's way to the profile). The raw-button
   reset in style.css (`button:not(.v-btn)`, font and colour inherit) outranks `.t-title`,
   so the title's size, weight and ink are restated here, where the scoped selector wins. */
.member-name {
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  text-align: start;
}

.member-row:hover,
.agent-row:hover {
  border-color: var(--line-2);
}

.min-w-0 {
  min-width: 0;
}

.pending-transfer {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.section-head {
  display: flex;
  gap: 8px;
}

/* 查到的那个人：一行头像 + 名字，压在输入框下面，两边都留了呼吸。 */
.found-user {
  display: flex;
  padding: 8px 10px;
  background: var(--fill);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  align-items: center;
}

.found-user__name {
  font-weight: 500;
  color: var(--ink);
}

/* 私聊按钮 + 它右上角那颗未读。按钮本身是 icon 按钮，徽标压在图标的右上角——
   不是按钮框的：small 按钮比图标大一圈，贴框角会浮在图标上方。这个槽是定位参照系。 */
.dm-slot {
  display: inline-flex;
  position: relative;
}

/* 未读 = 裸的琥珀数字，没有底色：侧栏那颗徽标同款，同一个产品里未读只能有一种读法。 */
.dm-unread {
  position: absolute;
  top: 4px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  color: var(--accent);
  pointer-events: none;
  inset-inline-end: 4px;
  font-variant-numeric: tabular-nums;
}

/* 「来自团队 X」是一行 14px 高的链接，触屏上够不到 44。撑开能点的那块（设计系统
   §10.1），画出来的样子不变——它上面那行 handle 是文字不是控件，压上去没有歧义。 */
@media (pointer: coarse) {
  .member-team-link {
    position: relative;
  }

  .member-team-link::before {
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    content: '';
    transform: translate(-50%, -50%);
  }
}
</style>
