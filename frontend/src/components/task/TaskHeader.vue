<script setup lang="ts">
// 任务页的页头，和房间页头是同一条线：同样的高度和底线，手机上同样填进顶栏那一格。
// 左边是「# 频道 / 任务名」、任务此刻的状态和负责人的头像；点任务名或头像打开「任务
// 信息」（改名、转交、协作者、AI 队友、环境、状态、编号、关闭都在里面）。对这件任务能做
// 的事只有一份（commands/taskActions.ts）：桌面上在侧栏右键和命令面板里，手机上在顶栏
// 的 ⋯ 里，编辑器那一种摆法里在那一组的 ⋯ 里。
//
// 桌面上页面几块并排摆开时，还有「对话 / 编辑器」两种摆法的开关（`form`）。对话那一种里，右边
// 是框在一起的一组按钮，一样东西一颗（概览、改动、文件、终端，现场和预览在 ⋯ 里），开着的是按下的
// 样子（`blocks`）；编辑器那一种里是三颗：左栏、下面（终端和现场）、对话，各自藏起或摆回那一块
// （`editorParts`），⋯ 也在这一组里，里面多了概览、预览两项（`blocks`，点了在中间打开）。
// 那一组排在这一行最后，只有提交按钮在它后面。平板横放的浮层那一档没有摆法可选，照旧是「概览」那颗
// 面板开关。
//
// 编辑器那一种里文件是主角，页头只留任务名和状态：有未提交的修改、负责人收在任务名旁边那一颗里
// （点开就是任务信息）。
import type { MenuCommand } from '@/commands'
import type { MenuAction } from '@/components/common/menuAction'
import type { RoomTask, Topic, TopicMemberRow } from '@/cx_types'
import type { HandInAction } from '@/lib/handIn'
import type { Environment, SessionEnvironment, TopicEnvironment } from '@/types/compute'
import type { TaskBlockButton } from './taskBlocks'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import { menuActionOf, useCommands } from '@/commands'
import { takesPartInTask, taskActions } from '@/commands/taskActions'
import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import PageSidebarToggle from '@/components/common/Navigation/PageSidebarToggle.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import EnvironmentMenu from '@/components/EnvironmentMenu.vue'
import PanelToggle from '@/components/room/PanelToggle.vue'
import CloseTaskDialog from '@/components/task/CloseTaskDialog.vue'
import TaskStateFacts from '@/components/task/TaskStateFacts.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { workPaneId } from '@/components/work/workLayout'
import { t } from '@/i18n'
import { environmentName } from '@/lib/environment'
import { relTime } from '@/lib/relTime'
import { taskState } from '@/lib/taskState'
import { taskTitle, topicShortId, topicTitle } from '@/lib/topicState'
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
    /** 有不在最近那一版里的修改：写「有未提交的修改」。 */
    unhanded?: boolean
    /** 给哪颗提交按钮（「提交审阅」「再提交一版」）；null 不给。 */
    handIn?: HandInAction
    /** 读者挪过这一页的摆法时给：那一组的 ⋯ 里（手机上是顶栏的 ⋯ 里）多一项「恢复默认摆法」。 */
    resetLayout?: () => void
    /** 「概览」开合的那几块的 id（见 PanelToggle）。 */
    panelControls?: string
    /** 此刻摆的是哪一种（对话 / 编辑器）；没得选（手机、平板横放）时 null，不摆开关。 */
    form?: 'chat' | 'editor' | null
    /** 编辑器里左栏、下面（终端和现场）、对话三块各自在不在屏幕上。 */
    editorParts?: { left: boolean; terminal: boolean; conversation: boolean }
    /** 对话那一种摆法里那一组按钮；编辑器里是 ⋯ 里在中间打开的那几样。 */
    blocks?: TaskBlockButton[]
  }>(),
  // 不给就是连着的：Vue 会把没传的布尔 prop 当成 false，那样每个页头都会写「未连接」。
  {
    connected: true,
    handIn: null,
    copyLink: undefined,
    resetLayout: undefined,
    panelControls: undefined,
    form: null,
    editorParts: undefined,
    blocks: () => [],
  }
)

const emit = defineEmits<{
  (e: 'open-room'): void
  (e: 'toggle-panel'): void
  (e: 'hand-in'): void
  (e: 'form', form: 'chat' | 'editor'): void
  (e: 'toggle-part', part: 'left' | 'terminal' | 'conversation'): void
  /** 对话那一种摆法里点了一颗：开着的关掉，关着的打开。 */
  (e: 'toggle-block', key: string): void
}>()

const formOptions = computed(() => [
  { value: 'chat' as const, label: t('work.task.form.chat') },
  { value: 'editor' as const, label: t('work.task.form.editor') },
])
/** 编辑器里页头右边那几颗：左栏、终端、对话。 */
const partButtons = computed(() => {
  const parts = props.editorParts
  if (props.form !== 'editor' || !parts) return []
  return [
    { part: 'left' as const, icon: 'mdi-dock-left', label: t('work.task.form.left'), on: parts.left },
    {
      part: 'terminal' as const,
      icon: 'mdi-dock-bottom',
      label: t('work.task.form.terminal'),
      on: parts.terminal,
    },
    {
      part: 'conversation' as const,
      icon: 'mdi-dock-right',
      label: t('work.task.form.conversation'),
      on: parts.conversation,
    },
  ]
})
/** 编辑器那一种：页头只留任务名和状态，别的收进任务名旁边那一颗。 */
const editorHead = computed(() => props.form === 'editor' && mdAndUp.value)
/** 编辑器里 ⋯ 里在中间打开的那几样。 */
const editorOpens = computed(() => (props.form === 'editor' ? props.blocks : []))
/** 编辑器里那一组的 ⋯ 由别处的按钮打开、挨着它摆。 */
const moreOpen = ref(false)
// The menu closes itself on a press outside it, this button included: a press on the button
// while it is open is a close, not a second open.
const moreWasOpen = ref(false)
const moreEl = ref<HTMLElement | null>(null)
const elementOf = (el: unknown): HTMLElement | null =>
  el instanceof HTMLElement ? el : ((el as { $el?: unknown } | null)?.$el as HTMLElement | null) ?? null

/** 对话那一种摆法里那一组：直接摆出来的几颗，和收在 ⋯ 里的几样。 */
const blockButtons = computed(() => (props.form === 'chat' ? props.blocks.filter((block) => !block.more) : []))
const moreBlocks = computed(() => (props.form === 'chat' ? props.blocks.filter((block) => block.more) : []))

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
// 状态那一格（`lib/taskState.ts`）和它后面那几段：在做什么、已做多久，在等什么，卡在哪
// （没留话时说一句打开看看），待谁审阅、第几版。桌面页头只摆状态那一格，那几段各自成一格
// 写在任务信息的「状态」里；手机顶栏没有别处，照旧写在第二行。队友报的原话照写，悬停看
// 谁什么时候报的。它报完之后会话结束了，旁边写「已离开」，不改它报的。
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
const metaEl = ref<HTMLElement | null>(null)
watch(
  () => state.value?.line,
  () => {
    if (metaEl.value) metaEl.value.scrollLeft = 0
  }
)
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
function openRoomFromInfo() {
  detailsOpen.value = false
  emit('open-room')
}

const closeOpen = ref(false)
const closing = ref(false)
async function confirmClose(conclusion: string) {
  closing.value = true
  try {
    if (await props.close(conclusion)) closeOpen.value = false
  } finally {
    closing.value = false
  }
}
async function reopenFromInfo() {
  if (await props.reopen()) detailsOpen.value = false
}
const canReopen = computed(() => !!props.task && props.task.status !== 'open' && props.worksTasks === true)
const canHandOver = computed(() => isOwner.value && isOpen.value && otherPeople.value.length > 0)
const shortId = computed(() => topicShortId(props.task?.id))

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

// 对这件任务能做的事：和侧栏任务行的右键、命令面板是同一份（`commands/taskActions.ts`）。
// 权限在那一份里判，不再在这几个模板分支里各写一遍。
const menuActions = computed<MenuCommand[]>(() =>
  props.task
    ? taskActions(
        props.task,
        {
          rename: () => (renameOpen.value = true),
          close: () => (closeOpen.value = true),
          reopen: () => void props.reopen(),
        },
        { me: ME, copyLink: props.copyLink ?? null, worksTasks: props.worksTasks }
      )
    : []
)
/** 「恢复默认摆法」：跟在那一份后面（手机顶栏的 ⋯、编辑器那一组的 ⋯），对话那一种摆法里在
 *  那一组按钮的 ⋯ 里。 */
const resetCommand = computed<MenuCommand | null>(() =>
  props.resetLayout
    ? { id: 'room.resetLayout', title: t('shell.layout.reset'), icon: 'mdi-restore', run: props.resetLayout }
    : null
)
const allActions = computed<MenuCommand[]>(() => [
  ...menuActions.value,
  ...(resetCommand.value ? [{ ...resetCommand.value, divider: menuActions.value.length > 0 }] : []),
])
const sheetActions = computed<MenuAction[]>(() => allActions.value.map(menuActionOf))
// 命令面板里这一页的「操作」：同一份。
useCommands(() => menuActions.value)
/** 桌面上的 ⋯ 只在编辑器那一种摆法里（和概览、预览在一起）；手机顶栏上一直有。 */
const showsMenu = computed(() => editorOpens.value.length > 0 || allActions.value.length > 0)
// 两条既有用例按这两个 test id 找按钮：其余项不必有。
function actionTestId(id: string): string | undefined {
  if (id === 'task.rename') return 'task-rename'
  if (id === 'task.reopen') return 'task-reopen'
  return undefined
}
</script>

<template>
  <div v-guide-anchor="'task-page'" class="task-header" :class="{ 'task-header--bar': !mdAndUp }">
    <PageSidebarToggle v-if="mdAndUp" />
    <div class="task-header__text">
      <button v-if="mdAndUp" type="button" class="task-header__room t-title" @click="emit('open-room')">
        # {{ topicTitle(room) }}
      </button>
      <span v-if="mdAndUp" class="task-header__sep t-title" aria-hidden="true">/</span>
      <button
        v-if="mdAndUp && task?.owner_handle"
        type="button"
        class="task-header__title task-header__title--button t-title"
        :title="t('work.task.details')"
        data-testid="task-title"
        @click="detailsOpen = true"
      >
        {{ taskTitle(task) }}
      </button>
      <span v-else class="task-header__title t-title" :title="task ? taskTitle(task) : ''">{{
        task ? taskTitle(task) : ''
      }}</span>
      <span ref="metaEl" class="task-header__meta">
        <TaskStateMark
          v-if="state"
          class="task-header__state"
          data-testid="task-phrase"
          :title="progressTitle ?? (state.tip || undefined)"
          :state="state"
          :emphasis="task?.needs_my_action === true"
          :size="13"
        />
        <TaskStateFacts
          v-if="state?.parts.length && !mdAndUp"
          class="task-header__note"
          data-testid="task-progress-note"
          :data-user-content="progressNote ? '' : undefined"
          :title="progressTitle ?? state.line"
          :parts="state.parts"
        />
        <a
          v-if="isOpen && task?.deploy_blocked_url"
          class="task-header__run"
          :href="task.deploy_blocked_url"
          target="_blank"
          rel="noopener"
          data-testid="task-deploy-blocked-run"
          >{{ t('work.taskState.blockedRun') }}</a
        >
        <span v-if="agentLeft" class="task-header__left" data-testid="task-agent-left">{{
          t('work.task.progress.left', { name: leftName })
        }}</span>
        <span v-if="unhanded && isOpen && !mdAndUp" class="task-header__unhanded" data-testid="task-unhanded">{{
          t('work.room.accept.handIn.unhanded')
        }}</span>
        <span v-if="connected === false" class="task-header__disconnected" role="status">{{
          t('work.room.header.disconnected')
        }}</span>
      </span>
      <BaseButton
        v-if="editorHead && task?.owner_handle"
        kind="ghost"
        size="sm"
        icon="mdi-information-outline"
        class="task-header__info"
        :class="{ 'task-header__part--on': detailsOpen }"
        :title="t('work.task.details')"
        :aria-label="t('work.task.details')"
        data-testid="task-details"
        @click="detailsOpen = true"
      />
      <!-- The owner: a face on desktop, a face and a name in the phone's second line. Opens the task info. -->
      <button
        v-if="task?.owner_handle && !editorHead"
        type="button"
        class="task-header__owner"
        :title="t('work.task.ownerChip', { name: ownerName })"
        :aria-label="t('work.task.ownerChip', { name: ownerName })"
        data-testid="task-details"
        @click="detailsOpen = true"
      >
        <UserAvatar :size="mdAndUp ? 20 : 14" :name="ownerName" :avatar="avatarOf(task?.owner_handle)" />
        <span v-if="!mdAndUp" class="task-header__owner-name">{{ t('work.task.ownerChip', { name: ownerName }) }}</span>
        <span v-if="collaborators.length && !mdAndUp" class="task-header__helpers" aria-hidden="true">
          <UserAvatar
            v-for="handle in collaborators.slice(0, 3)"
            :key="handle"
            :size="14"
            :name="nameOf(handle)"
            :avatar="avatarOf(handle)"
            class="task-header__helper"
          />
        </span>
      </button>
    </div>

    <SegmentedControl
      v-if="mdAndUp && form"
      class="task-header__form"
      size="md"
      :model-value="form"
      :options="formOptions"
      :label="t('work.task.form.label')"
      data-testid="task-form"
      @update:model-value="(value: 'chat' | 'editor') => value !== form && emit('form', value)"
    />

    <AdaptiveDialog v-if="task?.owner_handle" v-model="detailsOpen" size="md" :title="taskTitle(task)">
      <div class="task-details">
        <dl class="task-details__list">
          <div class="task-details__row">
            <dt>{{ t('work.task.renameLabel') }}</dt>
            <dd class="task-details__pair">
              <span data-user-content>{{ taskTitle(task) }}</span>
              <button
                v-if="takesPart"
                type="button"
                class="task-details__edit"
                data-testid="task-info-rename"
                @click="renameOpen = true"
              >
                {{ t('work.channel.overview.edit') }}
              </button>
            </dd>
          </div>
          <div class="task-details__row">
            <dt>{{ t('work.task.owner') }}</dt>
            <dd class="task-details__pair">
              <UserRef :handle="task.owner_handle" />
              <button
                v-if="canHandOver"
                type="button"
                class="task-details__edit"
                data-testid="task-hand-over"
                @click="handOverOpen = true"
              >
                {{ t('work.task.handOver') }}
              </button>
            </dd>
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
          <div v-if="state" class="task-details__row" data-testid="task-info-state">
            <dt>{{ t('work.task.info.state') }}</dt>
            <dd class="task-details__facts">
              <TaskStateMark :state="state" :emphasis="task.needs_my_action === true" :size="13" />
              <!-- Each fact its own cell: who it waits on, the summary, the version, when it was handed in. -->
              <span
                v-for="(part, i) in state.parts"
                :key="i"
                class="task-details__fact"
                data-testid="task-state-part"
                :data-user-content="progressNote && part === progressNote ? '' : undefined"
                >{{ part }}</span
              >
              <span v-if="agentLeft" class="task-details__fact">{{
                t('work.task.progress.left', { name: leftName })
              }}</span>
              <span v-if="unhanded && isOpen" class="task-details__fact" data-testid="task-unhanded">{{
                t('work.room.accept.handIn.unhanded')
              }}</span>
            </dd>
          </div>
          <div class="task-details__row">
            <dt>{{ t('work.task.info.channel') }}</dt>
            <dd>
              <button type="button" class="task-details__link" @click="openRoomFromInfo">
                # {{ topicTitle(room) }}
              </button>
            </dd>
          </div>
          <div class="task-details__row">
            <dt>{{ t('work.task.info.number') }}</dt>
            <dd class="task-details__pair">
              <span class="task-details__mono" :title="task.id">#{{ task.number ?? shortId }}</span>
              <button v-if="copyLink" type="button" class="task-details__edit" @click="copyLink">
                {{ t('work.room.menu.copyLink') }}
              </button>
            </dd>
          </div>
          <div class="task-details__row">
            <dt>{{ t('work.task.info.created') }}</dt>
            <dd>{{ relTime(task.created_at) }}</dd>
          </div>
          <div v-if="(isOpen && takesPart) || canReopen" class="task-details__row">
            <dt>{{ isOpen ? t('work.task.close') : t('work.task.reopen') }}</dt>
            <dd>
              <BaseButton v-if="isOpen" kind="secondary" size="sm" data-testid="task-close" @click="closeOpen = true">{{
                t('work.task.close')
              }}</BaseButton>
              <BaseButton v-else kind="secondary" size="sm" data-testid="task-reopen" @click="reopenFromInfo">{{
                t('work.task.reopen')
              }}</BaseButton>
            </dd>
          </div>
        </dl>
      </div>
    </AdaptiveDialog>

    <!-- Phones: the task's one list of actions under the top bar's ⋯. -->
    <AdaptiveMenu v-if="!mdAndUp && task && sheetActions.length" :actions="sheetActions" :title="taskTitle(task)">
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
    </AdaptiveMenu>
    <!-- The editor form: the ⋯ at the end of its group opens the overview and preview in the middle, then the
         same list of actions. -->
    <v-menu
      v-if="editorHead && task && showsMenu"
      v-model="moreOpen"
      :target="moreEl ?? undefined"
      location="bottom end"
    >
      <v-list density="compact" role="menu" :aria-label="t('work.task.more')">
        <template v-if="editorOpens.length">
          <v-list-item
            v-for="block in editorOpens"
            :key="block.key"
            role="menuitem"
            :title="block.label"
            :prepend-icon="block.icon"
            :data-task-block="block.key"
            @click="emit('toggle-block', block.key)"
          />
          <v-divider v-if="allActions.length" class="my-1" />
        </template>
        <template v-for="action in allActions" :key="action.id">
          <v-divider v-if="action.divider" class="my-1" />
          <v-list-item
            role="menuitem"
            :title="action.title"
            :prepend-icon="action.icon"
            :data-testid="actionTestId(action.id)"
            @click="action.run?.()"
          />
        </template>
      </v-list>
    </v-menu>
    <div v-if="mdAndUp && partButtons.length" class="task-header__parts" data-testid="task-parts">
      <BaseButton
        v-for="button in partButtons"
        :key="button.part"
        kind="ghost"
        size="sm"
        class="task-header__part"
        :class="{ 'task-header__part--on': button.on }"
        :icon="button.icon"
        :aria-label="button.label"
        :title="button.label"
        :aria-pressed="button.on ? 'true' : 'false'"
        @click="emit('toggle-part', button.part)"
      />
      <BaseButton
        v-if="task && showsMenu"
        :ref="(el: unknown) => (moreEl = elementOf(el))"
        kind="ghost"
        size="sm"
        icon="mdi-dots-horizontal"
        class="task-header__part"
        :title="t('work.task.more')"
        :aria-label="t('work.task.more')"
        data-testid="task-more"
        @pointerdown="moreWasOpen = moreOpen"
        @click="moreOpen = !moreWasOpen"
      />
    </div>
    <div
      v-else-if="mdAndUp && blockButtons.length"
      class="task-header__parts"
      role="group"
      :aria-label="t('work.task.form.blocks')"
      data-testid="task-blocks"
    >
      <span v-for="(block, i) in blockButtons" :key="block.key" class="task-header__block">
        <BaseButton
          kind="ghost"
          size="sm"
          class="task-header__part"
          :class="{ 'task-header__part--on': block.on }"
          :icon="block.icon"
          :aria-label="block.hint ?? block.label"
          :title="block.hint ?? block.label"
          :aria-pressed="block.on ? 'true' : 'false'"
          :aria-controls="workPaneId(block.key)"
          :data-panel-toggle="i === 0 ? '' : undefined"
          :data-task-block="block.key"
          @click="emit('toggle-block', block.key)"
        />
        <span v-if="block.count" class="task-header__count" aria-hidden="true">{{ block.count }}</span>
      </span>
      <v-menu v-if="moreBlocks.length || resetCommand" location="bottom end">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="ghost"
            size="sm"
            class="task-header__part"
            :class="{ 'task-header__part--on': moreBlocks.some((block) => block.on) }"
            icon="mdi-dots-horizontal"
            :aria-label="t('work.task.form.moreBlocks')"
            :title="t('work.task.form.moreBlocks')"
            data-testid="task-blocks-more"
          />
        </template>
        <v-list density="compact" role="menu" :aria-label="t('work.task.form.moreBlocks')">
          <v-list-item
            v-for="block in moreBlocks"
            :key="block.key"
            role="menuitemcheckbox"
            :aria-checked="block.on ? 'true' : 'false'"
            :title="block.label"
            :prepend-icon="block.icon"
            :append-icon="block.on ? 'mdi-check' : undefined"
            :data-task-block="block.key"
            @click="emit('toggle-block', block.key)"
          />
          <template v-if="resetCommand">
            <v-divider v-if="moreBlocks.length" class="my-1" />
            <v-list-item
              role="menuitem"
              :title="resetCommand.title"
              :prepend-icon="resetCommand.icon"
              @click="resetCommand.run?.()"
            />
          </template>
        </v-list>
      </v-menu>
    </div>
    <PanelToggle v-else-if="mdAndUp" :open="!!panelOpen" :controls="panelControls" @toggle="emit('toggle-panel')" />
    <span
      v-if="mdAndUp && !editorHead && unhanded && isOpen"
      class="task-header__unhanded"
      data-testid="task-unhanded"
      >{{ t('work.room.accept.handIn.unhanded') }}</span
    >
    <BaseButton v-if="handIn" kind="primary" size="sm" data-testid="task-hand-in" @click="emit('hand-in')">
      {{ handIn === 'resubmit' ? t('work.room.accept.handIn.resubmit') : t('work.room.accept.handIn.submit') }}
    </BaseButton>
  </div>

  <p v-if="actionError" class="task-notice t-meta" role="alert">{{ actionError }}</p>

  <CloseTaskDialog v-model="closeOpen" :loading="closing" @confirm="confirmClose" />
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
  min-width: 6em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 桌面上任务名是一颗按钮：点它打开任务信息，和频道页头点频道名同一个样子。 */
.task-header__title--button {
  flex: 0 1 auto;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.task-header__title--button:hover {
  color: var(--accent-ink);
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
.task-header__run {
  flex: none;
  color: var(--accent-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
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
.task-header__left,
.task-header__unhanded {
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
/* 两种摆法的开关：框在一起的两段，按下的那一段垫一层底。 */
.task-header__form {
  flex: none;
  gap: 0;
  padding: 0;
  overflow: hidden;
  background: transparent;
  border: 1px solid var(--line-2);
}
.task-header__form :deep(.segmented__option) {
  height: 26px;
  padding: 0 10px;
  border-radius: 0;
}
.task-header__form :deep(.segmented__option + .segmented__option) {
  border-left: 1px solid var(--line-2);
}
.task-header__form :deep(.segmented__option--on) {
  background: var(--fill-2);
}
/* 编辑器里任务名旁边那一颗：在等谁、有未提交的修改、负责人收在里面。 */
.task-header__info {
  flex: none;
  width: 26px;
  min-width: 0;
  height: 26px;
  color: var(--muted);
}
/* 框在一起的一组小图标按钮，和频道页头上那一组同一个样子。 */
.task-header__parts {
  display: inline-flex;
  flex: none;
  gap: 2px;
  align-items: center;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.task-header__parts .task-header__part {
  width: 30px;
  min-width: 0;
  height: 30px;
  --v-icon-size-multiplier: 0.85;
}
/* 「改动」角上改过几个文件，和编辑器左栏「改动」那一颗同一个记号。 */
.task-header__block {
  position: relative;
  display: inline-flex;
}
.task-header__count {
  position: absolute;
  top: -2px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  pointer-events: none;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 16px;
  color: var(--accent-ink);
  text-align: center;
  background: var(--accent-wash);
  border-radius: var(--radius-pill);
}
/* 按下的样子和「概览」开着时一样（PanelToggle）。 */
.task-header__part--on {
  background: var(--fill-2);
  color: var(--ink);
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
/* 状态那一格（在做什么、说要多久、谁离开了）可以缩到没有：主区被工作面板和助手面板
   夹窄时，它原来写着 `flex: none`，把整行撑得比容器还宽——任务名被压成 0 宽，状态
   那一句、负责人和状态标叠到一起。名字和负责人是不许让的（上面和下面各自有下限），
   让的是这一句。 */
.task-header__meta {
  display: flex;
  /* 放不下时先收这一段（在等谁、有没提交的修改），任务名最后才截。 */
  flex: 0 1000 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
}
/* 手机顶栏里两行：任务名独占第一行；状态和
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
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  gap: 6px;
}
.task-header--bar .task-header__meta::-webkit-scrollbar {
  display: none;
}
.task-header--bar .task-header__note {
  flex: none;
  max-width: none;
  overflow: visible;
  text-overflow: clip;
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
.task-details__list {
  margin: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.task-details__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 10px 14px;
}
.task-details__row + .task-details__row {
  border-top: 1px solid var(--line);
}
.task-details__row dt {
  flex: none;
  width: 72px;
  color: var(--muted);
}
/* 状态那一格：记号，然后一段一格（在等谁、第几版、什么时候提交），靠间距分开，不连成一句。 */
.task-details__facts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
}
.task-details__fact {
  color: var(--text);
}
.task-details__link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  cursor: pointer;
}
.task-details__link:hover {
  color: var(--accent-ink);
}
.task-details__mono {
  font-family: var(--font-mono);
  font-size: 13px;
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
