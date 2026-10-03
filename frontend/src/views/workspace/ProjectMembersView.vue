<script setup lang="ts">
// 「成员」—— 这个项目里有谁、他们是怎么在这里的，以及对他们能做什么。
//
// 一个项目的人只有三种来路，这一页就按来路分段：
//   - 所有者：建这个项目的人；
//   - 团队成员：项目所属团队里的每一个人，自动就在——这一页对他们不给任何**管理**
//     动作（移出是给不到他们的，后端那边没有一条可删的名册行），但他们自己能退出这
//     个项目（退的是这个项目，不是小队）；
//   - 外部成员：团队以外、被点名邀请进这一个项目的人，名字旁边挂「外部」。只有他们
//     能从这里被移出。
// 没有项目自己的角色：管理外部成员的是项目所有者和团队的所有者、管理员，由后端在
// 项目上给出的 `can_manage_members` 说了算（后端动手时按同一条规则再判一次）。它只在
// `GET /projects/{id}` 上有，项目列表不带，所以这一页自己问一次。
//
// 一行 = 一个人 = 两件事：找到他（点开是他的主页，右边是私聊），和——如果他是外部成员
// 而你管得了——把他移出。
//
// 右上角是「自己和这个项目的关系怎么结束」：谁都能「退出项目」（后端
// `DELETE /projects/{id}/membership` 认的恒是当前身份那个人），只有所有者不行——他
// 换一颗「转让项目」（他一走项目就没人管，得先把手交出去）。团队成员退的也是**这个
// 项目**：他还在小队里，小队别的项目照常，回来要人再请一次。
import type { LookedUpUser } from '@/api'
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectAgent, ProjectInvitation, ProjectMemberRow } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { provideRevealGate } from '@/composables/useRevealGate'

import {
  ApiError,
  getProject,
  inviteExternalMember,
  listProjectAgents,
  listProjectInvitations,
  lookupUser,
  removeProjectMember,
  revokeInvitation,
} from '@/api'
import { useCommands } from '@/commands'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import ExternalTag from '@/components/common/ExternalTag.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import LeaveProjectDialog from '@/components/LeaveProjectDialog.vue'
import TransferProjectDialog from '@/components/TransferProjectDialog.vue'
import { t } from '@/i18n'
import { memberName, teammateName } from '@/lib/agentNames'
import { agentDmKey } from '@/lib/dm'
import { isExternalMember } from '@/lib/externalMembers'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

defineOptions({ name: 'ProjectMembersView' })

const props = defineProps<{ projectId: string }>()
const router = useRouter()
const store = useWorkspaceStore()
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
const canManage = ref(false)
watch(
  () => props.projectId,
  async (id) => {
    canManage.value = false
    canManage.value = (await getProject(id)).can_manage_members === true
  },
  { immediate: true }
)

function unreadWith(handle: string): number {
  return store.privateUnreadMap?.[handle] ?? 0
}

// 99 以上不再往上数：徽标的宽度会把旁边的东西挤走。侧栏那一颗用的是同一条规矩。
function countLabel(n: number): string {
  return n > 99 ? '99+' : String(n)
}

// AI 队友这一段读的是队友列表，不是名册上那几行：私聊地址、未读键和「默认」那颗标
// 问的都是队友本身，名册行给不出来。停用的队友不列。
const teammates = ref<ProjectAgent[]>([])
const releaseTeammates = gate.hold()
watch(
  () => props.projectId,
  async (pid) => {
    teammates.value = []
    try {
      const rows = (await listProjectAgents(pid)).data
      if (props.projectId !== pid) return
      teammates.value = rows.filter((a) => a.is_active)
    } catch {
      // 拿不到就不显示这一段，名册不该被一个可选接口拖垮。
    } finally {
      releaseTeammates()
    }
  },
  { immediate: true }
)

// 发出去还没被答复的邀请：这些人**还不在名册上**，要他们自己点头才进来。
const invitations = ref<ProjectInvitation[]>([])
const revoking = ref<string | null>(null)
async function refreshInvitations() {
  const pid = props.projectId
  try {
    const payload = await listProjectInvitations(pid)
    if (props.projectId === pid) invitations.value = payload.data
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
  return memberName(m).toLowerCase().includes(q) || m.user_handle.toLowerCase().includes(q)
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

// 按来路分段。所有者那一行后端补在名册上（source=owner）；名册还没带 source 的那一刻
// 按 owner_handle 认，别让所有者掉进「团队成员」里。
const sections = computed(() => {
  const owner = people.value.filter((m) => m.source === 'owner' || m.user_handle === ownerHandle.value)
  const external = people.value.filter((m) => isExternalMember(m) && m.user_handle !== ownerHandle.value)
  const team = people.value.filter((m) => !owner.includes(m) && !external.includes(m))
  return [
    { key: 'owner', title: t('work.members.sectionOwner'), rows: owner },
    { key: 'team', title: t('work.members.sectionTeam'), rows: team },
    { key: 'external', title: t('work.members.sectionExternal'), rows: external },
  ].filter((s) => s.rows.length > 0)
})

// 能移出的只有外部成员，而且不是自己（自己走用右上角那颗「退出项目」）。
function removable(m: ProjectMemberRow): boolean {
  return canManage.value && isExternalMember(m) && m.user_handle !== me.value
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

function messageOf(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback
}

// ---- 移出外部成员 ----
const removeTarget = ref<ProjectMemberRow | null>(null)
function memberActions(m: ProjectMemberRow): MenuAction[] {
  return [
    {
      key: 'remove',
      label: t('work.members.remove'),
      icon: 'mdi-account-remove-outline',
      danger: true,
      onSelect: () => (removeTarget.value = m),
    },
  ]
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
const isOwner = computed(() => !!me.value && !!ownerHandle.value && me.value === ownerHandle.value)
const canLeave = computed(() => {
  const row = store.members.find((m) => m.user_handle === me.value)
  return !!row && !isOwner.value
})
const canTransfer = computed(() => project.value !== null && (isOwner.value || canManage.value))

// ---- 邀请外部成员 ----
// 像飞书加外部联系人：填完整的用户名或邮箱，先把查到的人摆出来确认，再发邀请。只认
// 精确匹配——邀请是把人放进项目的动作，「我以为我请的是他」这种错必须在按下按钮之
// 前就露出来。
const inviteOpen = ref(false)
const inviteQuery = ref('')
const inviting = ref(false)
const lookingUp = ref(false)
const found = ref<LookedUpUser | null>(null)
const lookupError = ref<string | null>(null)
let lookupTimer: ReturnType<typeof setTimeout> | null = null
let lookupSeq = 0

async function runLookup(raw: string) {
  const q = raw.trim()
  found.value = null
  lookupError.value = null
  if (!q) return
  const seq = ++lookupSeq
  lookingUp.value = true
  try {
    const user = await lookupUser(q)
    // 打字比请求快：只认最后一次发出去的那个，否则先回来的旧结果会盖掉新的。
    if (seq !== lookupSeq) return
    found.value = user
  } catch (e) {
    if (seq !== lookupSeq) return
    lookupError.value =
      e instanceof ApiError && e.status === 404 ? t('work.members.notFound') : messageOf(e, t('work.members.failed'))
  } finally {
    if (seq === lookupSeq) lookingUp.value = false
  }
}

watch(inviteQuery, (raw) => {
  if (lookupTimer) clearTimeout(lookupTimer)
  lookupTimer = setTimeout(() => void runLookup(raw), 350)
})

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
    await inviteExternalMember(props.projectId, user.handle)
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
  ...(canTransfer.value
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
        <div class="t-eyebrow mb-2">{{ s.title }} · {{ s.rows.length }}</div>
        <p v-if="s.key === 'team'" class="t-meta-read mb-2">{{ t('work.members.teamHint') }}</p>
        <v-card v-for="m in s.rows" :key="m.user_handle" class="mb-2 member-row" variant="outlined">
          <div class="d-flex align-center pa-3" @click="openProfile(m)">
            <UserAvatar :name="memberName(m) || m.user_handle" :avatar="faceUrl(m)" :size="36" class="mr-3" />
            <div class="min-w-0">
              <div class="d-flex align-center ga-2">
                <span class="t-title text-truncate">{{ memberName(m) || m.user_handle }}</span>
                <ExternalTag v-if="s.key === 'external'" />
                <span v-if="m.user_handle === me" class="chip-neutral">{{ t('work.members.me') }}</span>
              </div>
              <div class="t-meta c-muted">@{{ m.user_handle }}</div>
              <router-link
                v-if="s.key === 'team' && m.team_handle"
                :to="{ name: 'TeamsDetail', params: { handle: m.team_handle } }"
                class="t-meta-read"
                @click.stop
                >{{ t('work.members.fromTeam', { handle: m.team_handle }) }}</router-link
              >
            </div>
            <v-spacer />
            <span v-if="m.user_handle !== me" class="dm-slot">
              <v-btn
                variant="text"
                color="on-surface-variant"
                size="small"
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
            <AdaptiveMenu v-if="removable(m)" :actions="memberActions(m)" :title="memberName(m) || m.user_handle">
              <template #activator="{ props: menuProps }">
                <v-btn
                  v-bind="menuProps"
                  variant="text"
                  color="on-surface-variant"
                  size="small"
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
        <div class="t-eyebrow mb-2">{{ t('work.members.sectionPending') }} · {{ invitations.length }}</div>
        <v-card v-for="inv in invitations" :key="inv.id" class="mb-2" variant="outlined">
          <div class="d-flex align-center pa-3">
            <UserAvatar :name="inv.invitee_handle" :size="36" class="mr-3" />
            <div class="min-w-0">
              <div class="d-flex align-center ga-2">
                <span class="t-title text-truncate">@{{ inv.invitee_handle }}</span>
                <ExternalTag />
              </div>
              <i18n-t keypath="work.members.pendingBy" tag="div" class="t-meta c-muted">
                <template #inviter><UserRef :handle="inv.inviter_handle" /></template>
              </i18n-t>
            </div>
            <v-spacer />
            <v-btn
              v-if="canManage"
              variant="text"
              color="on-surface-variant"
              size="small"
              :loading="revoking === inv.id"
              @click="takeBack(inv)"
            >
              {{ t('work.members.revoke') }}
            </v-btn>
          </div>
        </v-card>
      </div>

      <div v-if="agents.length" class="mb-6">
        <div class="t-eyebrow mb-2">{{ t('work.members.sectionAgents') }} · {{ agents.length }}</div>
        <v-card v-for="a in agents" :key="a.handle" class="mb-2 agent-row" variant="outlined">
          <div class="d-flex align-center pa-3">
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
              <div class="t-meta c-muted">@{{ a.handle }}</div>
            </div>
            <v-spacer />
            <span class="dm-slot">
              <v-btn
                variant="text"
                color="on-surface-variant"
                size="small"
                icon="mdi-message-outline"
                :aria-label="t('work.members.dm')"
                :title="t('work.members.dm')"
                @click.stop="openAgentDm(a)"
              />
              <span v-if="unreadWith(agentDmKey(a.handle)) > 0" class="dm-unread">
                {{ countLabel(unreadWith(agentDmKey(a.handle))) }}
              </span>
            </span>
            <v-btn
              variant="text"
              color="on-surface-variant"
              size="small"
              @click="
                router.push({ name: 'project-settings', params: { projectId: props.projectId, section: 'agents' } })
              "
            >
              {{ t('work.members.agentSettings') }}
            </v-btn>
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
      <div v-if="found" class="found-user mb-2" data-testid="found-user">
        <UserAvatar
          :name="found.name || found.handle"
          :avatar="found.avatar_id == null ? '' : getAvatarUrl(found.avatar_id)"
          :size="32"
          class="mr-3"
        />
        <div class="min-w-0">
          <div class="t-body found-user__name">{{ found.name || found.handle }}</div>
          <div class="t-meta c-muted">@{{ found.handle }}</div>
        </div>
        <v-spacer />
        <span v-if="alreadyIn" class="t-meta c-muted">{{ t('work.members.alreadyIn') }}</span>
      </div>
    </AdaptiveDialog>

    <LeaveProjectDialog v-model="leaveOpen" :project-id="props.projectId" />
    <TransferProjectDialog v-model="transferOpen" :project-id="props.projectId" />

    <v-dialog :model-value="removeTarget !== null" max-width="420" @update:model-value="removeTarget = null">
      <v-card>
        <v-card-title class="t-dialog-title pt-4">{{
          t('work.members.removeTitle', { name: memberName(removeTarget) || removeTarget?.user_handle || '' })
        }}</v-card-title>
        <v-card-text class="t-body c-muted">{{ t('work.members.removeBody') }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="removeTarget = null">{{
            t('work.members.cancel')
          }}</v-btn>
          <v-btn color="error" variant="flat" @click="confirmRemove">{{ t('work.members.confirmRemove') }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </AppPage>
</template>

<style scoped>
.member-row {
  cursor: pointer;
}
.member-row:hover {
  border-color: var(--line-2);
}
.min-w-0 {
  min-width: 0;
}
/* 查到的那个人：一行头像 + 名字，压在输入框下面，两边都留了呼吸。 */
.found-user {
  display: flex;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
}
.found-user__name {
  font-weight: 500;
  color: var(--ink);
}
/* 私聊按钮 + 它右上角那颗未读。按钮本身是 icon 按钮，徽标压在图标的右上角——
   不是按钮框的：small 按钮比图标大一圈，贴框角会浮在图标上方。这个槽是定位参照系。 */
.dm-slot {
  position: relative;
  display: inline-flex;
}
/* 未读 = 裸的琥珀数字，没有底色：侧栏那颗徽标同款，同一个产品里未读只能有一种读法。 */
.dm-unread {
  position: absolute;
  top: 4px;
  inset-inline-end: 4px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  pointer-events: none;
}
</style>
