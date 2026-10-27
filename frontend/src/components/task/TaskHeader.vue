<script setup lang="ts">
// 任务页的页头，和房间页头是同一条线：同样的高度和底线，手机上同样填进顶栏那一格。
// 左边是「# 房间 / 任务名」和任务此刻的状态；右边是负责人和协作者（点开看谁在做、
// 在哪台电脑上做，负责人在这里增减协作者），以及 ⋯（重命名、关闭归负责人和协作者，
// 转交只归负责人；关了的任务，角色有「新建任务和审阅」的人都能重新打开）。
import type { MenuCommand } from '@/commands'
import type { RoomTask, Topic, TopicMemberRow } from '@/cx_types'
import type { Environment, SessionEnvironment, TopicEnvironment } from '@/types/compute'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import { takesPartInTask, taskActions } from '@/commands/taskActions'
import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import EnvironmentMenu from '@/components/EnvironmentMenu.vue'
import PanelToggle from '@/components/room/PanelToggle.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { t } from '@/i18n'
import { environmentName } from '@/lib/environment'
import { relTime } from '@/lib/relTime'
import { taskState } from '@/lib/taskState'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'

const props = withDefaults(
  defineProps<{
    room: Topic
    task: RoomTask | null
    memberNames: Record<string, string>
    agentName: string
    people: TopicMemberRow[]
    /** 这间房名册上的 AI 队友：负责人从这里挑一位做这件事。 */
    agents: TopicMemberRow[]
    machine: TopicEnvironment | null
    machineError: boolean
    actionError: string | null
    connected?: boolean
    close: (conclusion: string) => Promise<boolean>
    reopen: () => Promise<boolean>
    handOver: (owner: string) => Promise<boolean>
    rename: (title: string) => Promise<boolean>
    setCollaborators: (handles: string[]) => Promise<boolean>
    setAgent: (handle: string | null) => Promise<boolean>
    loadMachine: () => Promise<void>
    /** 换这个任务里一条会话的环境；换「新会话默认」。 */
    setMachine: (sessionId: string, env: Environment) => Promise<{ proposal?: { content: string } | null }>
    setMachineDefault: (env: Environment) => Promise<{ proposal?: { content: string } | null }>
    /** 右侧面板是不是开着——「概览」那颗开关读它。 */
    panelOpen?: boolean
    /** 复制这件任务的链接。页面给：这一层不认路由（组件只凭 props 画）。 */
    copyLink?: () => void
    /** 我的角色有「新建任务和审阅」：关了的任务我能重新打开。页面从项目的 `my_permissions` 读。 */
    worksTasks?: boolean
  }>(),
  // 不给就是连着的：Vue 会把没传的布尔 prop 当成 false，那样每个页头都会写「未连接」。
  { connected: true }
)

const emit = defineEmits<{
  (e: 'open-room'): void
  (e: 'toggle-panel'): void
}>()

const { mdAndUp } = useDisplay()
const ME = myHandle()
const isOwner = computed(() => !!props.task && props.task.owner_handle === ME)
const isOpen = computed(() => props.task?.status === 'open')
const takesPart = computed(() => !!props.task && takesPartInTask(props.task, ME))
const ownerName = computed(() => {
  const handle = props.task?.owner_handle ?? ''
  return props.memberNames[handle] || handle
})
const taskAgentName = computed(() => {
  const handle = props.task?.agent_handle
  return (handle && props.memberNames[handle]) || props.agentName
})
// 状态那一格（`lib/taskState.ts`）和它后面那一句：在做什么、已做多久，在等什么，卡在哪
// （没留话时说一句打开看看），待谁审阅、第几版。队友报的原话照写，悬停看谁什么时候报的。
// 它报完之后会话结束了，旁边写「已离开」，不改它报的。
const state = computed(() =>
  props.task ? taskState(props.task, { viewer: ME, nameOf, agentName: taskAgentName.value }) : null
)
const progressNote = computed(() => (isOpen.value ? props.task?.progress_note ?? '' : ''))
const agentLeft = computed(() => isOpen.value && !!props.task?.progress_state && !!props.task?.agent_left_at)
// 「已离开」说的是报那一句的那位，不是任务现在的队友：任务换过人时，两处名字不是同一个，
// 照着任务当前的队友写，就把「离开」栽到了别人的头上。
const leftName = computed(() => {
  const by = props.task?.progress_set_by
  if (!by) return taskAgentName.value
  // 名册上查不到就用 handle 本身：退回默认的队友名字等于又把别人的名字栽上去。
  return props.memberNames[by] || by
})
const progressTitle = computed(() => {
  const by = props.task?.progress_set_by
  const at = props.task?.progress_set_at
  if (!isOpen.value || !by || !at) return undefined
  return t('work.task.progress.said', { name: props.memberNames[by] || taskAgentName.value, time: relTime(at) })
})
const otherPeople = computed(() => props.people.filter((m) => m.member_handle !== ME))
const collaborators = computed(() => props.task?.contributor_handles ?? [])
const nameOf = (handle: string) => props.memberNames[handle] || handle
// 这个人自己挑过的头像。没挑过（`avatar_id` 是 null）给空串，交给 `UserAvatar` 画按
// handle 派生的彩色首字母：`getAvatarUrl(undefined)` 回的是全站默认那张脸，所有没挑过
// 头像的人共用同一张，认不出是谁。
function avatarOf(handle: string | null | undefined): string {
  const chosen = props.people.find((m) => m.member_handle === handle)?.avatar_id
  return chosen ? getAvatarUrl(chosen) : ''
}
// 还能拉进来的人：名册上的人，除了负责人和已经在协作的。
const addable = computed(() =>
  props.people.filter(
    (m) => m.member_handle !== props.task?.owner_handle && !collaborators.value.includes(m.member_handle)
  )
)
const adding = ref('')
async function addCollaborator() {
  if (!adding.value) return
  if (await props.setCollaborators([...collaborators.value, adding.value])) adding.value = ''
}
function removeCollaborator(handle: string) {
  void props.setCollaborators(collaborators.value.filter((h) => h !== handle))
}

// 做这件事的队友：菜单里是这间房名册上的队友（后端换的时候也只认名册上那个座位），
// 加上「跟随频道」——不给这件事单独指定，房间的那位就接着做。
const agentOptions = computed<{ handle: string | null; name: string }[]>(() => {
  const current = props.task?.agent_handle ?? ''
  return [
    { handle: null, name: t('work.task.followsRoom') },
    // 现在指着的那一位即使已经不在名册上，也留在菜单里——不然这一格就再也改不回他
    // 头上，也看不出这件事现在交给了谁。
    ...(current ? [{ handle: current, name: nameOf(current) }] : []),
    ...props.agents
      .filter((m) => m.member_handle !== current)
      .map((m) => ({ handle: m.member_handle, name: nameOf(m.member_handle) })),
  ]
})
// 只有一项可挑（除了「跟随频道」没别人）就不给这颗「改」——和环境那行同一道判断。
const canPickAgent = computed(() => isOwner.value && isOpen.value && agentOptions.value.length > 1)
const agentOpen = ref(false)
const swappingAgent = ref(false)
async function pickAgent(handle: string | null) {
  if ((props.task?.agent_handle ?? null) === handle) {
    agentOpen.value = false
    return
  }
  swappingAgent.value = true
  try {
    // 换成了才收菜单。后端没让换就留着，让他再挑一次——理由写在页头下面那一行。
    if (await props.setAgent(handle)) agentOpen.value = false
  } finally {
    swappingAgent.value = false
  }
}

// 这个任务的会话各有各的环境，各改各的；「新会话默认」只管以后开工的。
function saveSession(session: SessionEnvironment) {
  return (env: Environment) => props.setMachine(session.session_id, env)
}
function saveDefault(env: Environment) {
  return props.setMachineDefault(env)
}
const sessionName = (session: SessionEnvironment) => props.memberNames[session.seat_handle] || session.seat_handle

const detailsOpen = ref(false)
watch(detailsOpen, (open) => {
  if (open) void props.loadMachine()
})

const closeOpen = ref(false)
const closing = ref(false)
const conclusion = ref('')
async function confirmClose() {
  closing.value = true
  try {
    if (await props.close(conclusion.value)) closeOpen.value = false
  } finally {
    closing.value = false
  }
}

const renameOpen = ref(false)
const renaming = ref(false)
const newTitle = ref('')
watch(renameOpen, (open) => {
  if (open) newTitle.value = props.task?.title ?? ''
})
async function confirmRename() {
  renaming.value = true
  try {
    if (await props.rename(newTitle.value)) renameOpen.value = false
  } finally {
    renaming.value = false
  }
}

const handOverOpen = ref(false)
const handingOver = ref(false)
const nextOwner = ref('')
async function confirmHandOver() {
  handingOver.value = true
  try {
    if (await props.handOver(nextOwner.value)) handOverOpen.value = false
  } finally {
    handingOver.value = false
  }
}

// ⋯ 里的清单和侧栏任务行是同一份（`commands/taskActions.ts`）：这里露出全部，侧栏
// 只留「重命名」「复制链接」。权限也在那一份里判，不再在这几个模板分支里各写一遍。
const menuActions = computed<MenuCommand[]>(() =>
  props.task
    ? taskActions(
        props.task,
        {
          rename: () => (renameOpen.value = true),
          handOver: () => (handOverOpen.value = true),
          close: () => (closeOpen.value = true),
          reopen: () => void props.reopen(),
        },
        {
          me: ME,
          canHandOver: otherPeople.value.length > 0,
          copyLink: props.copyLink ?? null,
          worksTasks: props.worksTasks,
        }
      )
    : []
)
// ⋯ 给参加这件活的人，和能重新打开这件关了的任务的人；只剩「复制链接」时不摆。
const showsMenu = computed(() => takesPart.value || menuActions.value.some((action) => action.id === 'task.reopen'))
// 两条既有用例按这两个 test id 找按钮：其余项不必有。
function actionTestId(id: string): string | undefined {
  if (id === 'task.rename') return 'task-rename'
  if (id === 'task.reopen') return 'task-reopen'
  return undefined
}
</script>

<template>
  <div v-guide-anchor="'task-page'" class="task-header" :class="{ 'task-header--bar': !mdAndUp }">
    <div class="task-header__text">
      <button v-if="mdAndUp" type="button" class="task-header__room t-title" @click="emit('open-room')">
        # {{ topicTitle(room) }}
      </button>
      <span v-if="mdAndUp" class="task-header__sep t-title" aria-hidden="true">/</span>
      <span class="task-header__title t-title" :title="task ? taskTitle(task) : ''">{{
        task ? taskTitle(task) : ''
      }}</span>
      <span class="task-header__meta">
        <TaskStateMark
          v-if="state"
          class="task-header__state"
          data-testid="task-phrase"
          :title="progressTitle"
          :state="state"
          :emphasis="task?.needs_my_action === true"
          :size="13"
        />
        <span
          v-if="state?.line"
          class="task-header__note"
          data-testid="task-progress-note"
          :data-user-content="progressNote ? '' : undefined"
          :title="progressTitle ?? state?.line"
          >{{ state?.line }}</span
        >
        <span v-if="agentLeft && (mdAndUp || !state?.line)" class="task-header__left" data-testid="task-agent-left">{{
          t('work.task.progress.left', { name: leftName })
        }}</span>
        <span v-if="connected === false" class="task-header__disconnected" role="status">{{
          t('work.room.header.disconnected')
        }}</span>
      </span>
    </div>

    <v-menu v-if="task?.owner_handle" v-model="detailsOpen" location="bottom end" :close-on-content-click="false">
      <template #activator="{ props: menuProps }">
        <button
          type="button"
          class="task-header__owner"
          v-bind="menuProps"
          :title="t('work.task.details')"
          data-testid="task-details"
        >
          <UserAvatar :size="mdAndUp ? 20 : 14" :name="ownerName" :avatar="avatarOf(task?.owner_handle)" />
          <span class="task-header__owner-name">{{ t('work.task.ownerChip', { name: ownerName }) }}</span>
          <span v-if="collaborators.length" class="task-header__helpers" aria-hidden="true">
            <UserAvatar
              v-for="handle in collaborators.slice(0, 3)"
              :key="handle"
              :size="mdAndUp ? 18 : 14"
              :name="nameOf(handle)"
              :avatar="avatarOf(handle)"
              class="task-header__helper"
            />
          </span>
        </button>
      </template>
      <v-card class="task-details">
        <dl class="task-details__list t-meta">
          <div class="task-details__row">
            <dt>{{ t('work.task.owner') }}</dt>
            <dd><UserRef :handle="task.owner_handle" /></dd>
          </div>
          <div
            v-if="collaborators.length || (isOwner && isOpen)"
            class="task-details__row"
            data-testid="task-collaborators"
          >
            <dt :title="t('work.task.collaboratorsHint')">{{ t('work.task.collaborators') }}</dt>
            <dd class="task-details__people">
              <span v-for="handle in collaborators" :key="handle" class="task-details__person">
                <UserRef :handle="handle" />
                <button
                  v-if="isOpen && (isOwner || handle === ME)"
                  type="button"
                  class="task-details__retry"
                  @click="removeCollaborator(handle)"
                >
                  {{ handle === ME ? t('work.task.leaveCollaboration') : t('work.task.removeCollaborator') }}
                </button>
              </span>
              <span v-if="isOwner && isOpen && addable.length" class="task-details__person">
                <select
                  v-model="adding"
                  class="task-notice__select t-meta"
                  :aria-label="t('work.task.addCollaborator')"
                >
                  <option value="">{{ t('work.task.addCollaborator') }}</option>
                  <option v-for="m in addable" :key="m.member_handle" :value="m.member_handle">
                    {{ nameOf(m.member_handle) }}
                  </option>
                </select>
                <button v-if="adding" type="button" class="task-details__retry" @click="addCollaborator">
                  {{ t('work.task.addCollaborator') }}
                </button>
              </span>
            </dd>
          </div>
          <div class="task-details__row" data-testid="task-agent">
            <dt>{{ t('work.task.agent') }}</dt>
            <dd class="task-details__pair">
              <span>{{ taskAgentName }}</span>
              <span v-if="!task.agent_handle" class="task-details__tag">{{ t('work.task.followsRoom') }}</span>
              <v-menu v-if="canPickAgent" v-model="agentOpen" location="bottom end" :close-on-content-click="false">
                <template #activator="{ props: menuProps }">
                  <button
                    type="button"
                    class="task-details__edit"
                    v-bind="menuProps"
                    :title="t('work.task.agentEdit')"
                    data-testid="task-agent-edit"
                  >
                    {{ t('work.task.agentEdit') }}
                  </button>
                </template>
                <v-card class="task-agent-menu" data-testid="task-agent-menu">
                  <div class="task-agent-menu__heading">{{ t('work.task.agentHeading') }}</div>
                  <p class="task-agent-menu__hint">{{ t('work.task.agentHint') }}</p>
                  <button
                    v-for="option in agentOptions"
                    :key="option.handle ?? 'room'"
                    type="button"
                    class="task-agent-menu__row"
                    :disabled="swappingAgent"
                    @click="pickAgent(option.handle)"
                  >
                    <v-icon size="18">{{
                      (task.agent_handle ?? null) === option.handle ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'
                    }}</v-icon>
                    <span>{{ option.name }}</span>
                  </button>
                </v-card>
              </v-menu>
            </dd>
          </div>
          <div class="task-details__row" data-testid="task-machine">
            <dt>{{ t('work.task.machine') }}</dt>
            <dd v-if="machine" class="task-details__envs">
              <div
                v-for="session in machine.sessions"
                :key="session.session_id"
                class="task-details__pair"
                :data-testid="`session-env-${session.session_id}`"
              >
                <span>{{
                  t('compute.env.sessionLine', {
                    name: sessionName(session),
                    env: session.environment
                      ? environmentName(session.environment, machine.devices, machine.specs)
                      : t('compute.env.notStarted'),
                  })
                }}</span>
                <EnvironmentMenu
                  v-if="isOwner && isOpen"
                  :heading="t('compute.env.sessionHeading', { name: sessionName(session) })"
                  :hint="t('compute.env.sessionHint')"
                  :current="session.environment ?? machine.default"
                  :devices="machine.devices"
                  :specs="machine.specs"
                  :save="saveSession(session)"
                  @changed="loadMachine"
                />
              </div>
              <div class="task-details__pair" data-testid="task-env-default">
                <span>{{
                  t('compute.env.defaultHere', {
                    name: environmentName(
                      machine.default ?? { kind: 'sandbox', spec: 'standard' },
                      machine.devices,
                      machine.specs
                    ),
                  })
                }}</span>
                <span v-if="machine.default_from === 'room'" class="task-details__tag">{{
                  t('work.task.followsRoom')
                }}</span>
                <EnvironmentMenu
                  v-if="isOwner && isOpen"
                  :heading="t('compute.env.defaultHeading')"
                  :hint="t('compute.env.defaultHint')"
                  :current="machine.default"
                  :devices="machine.devices"
                  :specs="machine.specs"
                  :save="saveDefault"
                  @changed="loadMachine"
                />
              </div>
            </dd>
            <dd v-else-if="machineError">
              {{ t('compute.env.loadFailed') }}
              <button type="button" class="task-details__retry" @click="loadMachine">
                {{ t('global.loadError.retry') }}
              </button>
            </dd>
            <dd v-else><v-progress-circular indeterminate size="14" width="2" /></dd>
          </div>
        </dl>
      </v-card>
    </v-menu>

    <PanelToggle v-if="mdAndUp" :open="!!panelOpen" @toggle="emit('toggle-panel')" />
    <v-menu v-if="task && showsMenu" location="bottom end">
      <template #activator="{ props: menuProps }">
        <BaseButton
          v-bind="menuProps"
          icon="mdi-dots-horizontal"
          size="sm"
          class="tap-target"
          :title="t('work.task.more')"
          :aria-label="t('work.task.more')"
          data-testid="task-more"
        />
      </template>
      <v-list density="compact" role="menu" :aria-label="t('work.task.more')">
        <v-list-item
          v-for="action in menuActions"
          :key="action.id"
          role="menuitem"
          :title="action.title"
          :prepend-icon="action.icon"
          :data-testid="actionTestId(action.id)"
          @click="action.run?.()"
        />
      </v-list>
    </v-menu>
  </div>

  <p v-if="actionError" class="task-notice t-meta" role="alert">{{ actionError }}</p>

  <ConfirmDialog
    v-model="closeOpen"
    :title="t('work.task.closeTitle')"
    :confirm-label="t('work.task.close')"
    :loading="closing"
    @confirm="confirmClose"
  >
    <p class="t-body mb-2">{{ t('work.task.closeBody') }}</p>
    <textarea
      v-model="conclusion"
      rows="3"
      autocomplete="off"
      class="task-dialog__input t-body"
      :aria-label="t('work.task.conclusion')"
      :placeholder="t('work.task.conclusion')"
    />
  </ConfirmDialog>
  <ConfirmDialog
    v-model="renameOpen"
    :title="t('work.task.renameTitle')"
    :confirm-label="t('work.task.rename')"
    :loading="renaming"
    :disabled="!newTitle.trim()"
    @confirm="confirmRename"
  >
    <input
      v-model="newTitle"
      type="text"
      maxlength="80"
      autocomplete="off"
      class="task-dialog__input t-body"
      :aria-label="t('work.task.renameLabel')"
      @keydown.enter.prevent="newTitle.trim() && confirmRename()"
    />
  </ConfirmDialog>
  <ConfirmDialog
    v-model="handOverOpen"
    :title="t('work.task.handOverTitle')"
    :confirm-label="t('work.task.handOver')"
    :loading="handingOver"
    :disabled="!nextOwner"
    @confirm="confirmHandOver"
  >
    <select v-model="nextOwner" class="task-dialog__input t-body" :aria-label="t('work.task.handOverTo')">
      <option value="" disabled>{{ t('work.task.handOverTo') }}</option>
      <option v-for="m in otherPeople" :key="m.member_handle" :value="m.member_handle">
        {{ memberNames[m.member_handle] || m.member_handle }}
      </option>
    </select>
  </ConfirmDialog>
</template>

<style scoped>
.task-header__helpers {
  display: inline-flex;
  margin-left: 2px;
}
.task-header__helper + .task-header__helper {
  margin-left: -6px;
}
.task-details__people {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.task-details__person {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.task-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  /* 和房间页头同一条基线：高度和底线读同一个 token（TopicHeader）。 */
  height: var(--app-page-header-height);
  padding: 0 12px;
  background: var(--surface);
  border-bottom: var(--app-page-header-rule);
}
.task-header--bar {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  padding: 0;
  background: none;
  border-bottom: 0;
}
.task-header__text {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.task-header__room {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 40%;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: none;
  color: var(--muted);
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.task-header__room:hover {
  color: var(--ink);
}
.task-header__sep {
  flex: none;
  color: var(--faint);
}
.task-header__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 和房间页头的状态标同一个样子。 */
.task-header__state {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
/* 队友报状态时留的那一句：它的原话，可能很长，截在一行里，悬停看全（`title`）。 */
.task-header__note {
  min-width: 0;
  max-width: 32ch;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-header__left {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.task-header__disconnected {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 12px;
}
.task-header__owner {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px 0 4px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-header__owner:hover {
  background: var(--fill);
}
.task-header__owner-name {
  white-space: nowrap;
}
.task-header__meta {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
}
/* 手机顶栏里两行（和支线转成任务时的 ThreadHead 一个样子）：任务名独占第一行；状态和
   负责人退到第二行，同一号字、同一种灰，各成一格，靠间距和小头像分开，不拿点号串。
   负责人那颗照旧点开任务信息，⋯ 竖跨两行贴右。 */
.task-header--bar {
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(auto, 1fr) auto;
  grid-template-rows: auto auto;
  align-content: center;
  column-gap: 8px;
  row-gap: 1px;
}
.task-header--bar .task-header__text {
  display: contents;
}
.task-header--bar .task-header__title {
  grid-column: 1 / 3;
  grid-row: 1;
  max-width: 100%;
}
.task-header--bar .task-header__meta {
  grid-column: 1;
  grid-row: 2;
  min-width: 0;
  overflow: hidden;
  gap: 6px;
}
.task-header--bar .task-header__left {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-header--bar .task-header__state,
.task-header--bar .task-header__owner {
  padding: 0;
  background: none;
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
}
/* 手指要点得中：上下各撑出 8px，再用负外边距还回去，第二行不因此变高。 */
.task-header--bar .task-header__owner {
  grid-column: 2;
  grid-row: 2;
  justify-self: start;
  gap: 4px;
  height: auto;
  padding-block: 8px;
  margin-block: -8px;
}
.task-header--bar .task-header__owner-name {
  max-width: 8em;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task-header--bar :deep([data-testid='task-more']) {
  grid-column: 3;
  grid-row: 1 / 3;
  align-self: center;
}
.task-details {
  width: 320px;
  max-width: calc(100vw - 32px);
  padding: 8px 12px;
}
.task-details__list {
  margin: 0;
}
.task-details__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 4px 0;
}
.task-details__row dt {
  flex: none;
  width: 72px;
  color: var(--faint);
}
.task-details__row dd {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: var(--text);
}
.task-details__envs {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.task-details__pair {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.task-details__tag {
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
}
.task-details__retry {
  border: 0;
  background: transparent;
  color: var(--muted);
  text-decoration: underline;
  cursor: pointer;
}
/* 行尾那颗「改」，和环境那一行同一档小动作。 */
.task-details__edit {
  flex: none;
  margin-left: auto;
  padding: 0 2px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.task-details__edit:hover {
  color: var(--ink);
}
.task-agent-menu {
  width: 320px;
  max-width: calc(100vw - 32px);
  padding: 8px;
}
.task-agent-menu__heading {
  padding: 8px;
  font-size: 14px;
  font-weight: 600;
}
.task-agent-menu__hint {
  margin: 4px 8px 8px;
  color: var(--muted);
  font-size: 13px;
}
.task-agent-menu__row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 12px 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
.task-agent-menu__row:hover {
  background: var(--fill);
}
.task-agent-menu__row:disabled {
  cursor: wait;
}
.task-notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 0;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.task-notice__select {
  padding: 2px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink);
}
.task-dialog__input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--ink);
  resize: vertical;
}
</style>
