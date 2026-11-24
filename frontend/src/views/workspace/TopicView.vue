<script setup lang="ts">
import type { TaskBlockButton } from '@/components/task/taskBlocks'
import type { WorkTabMeta } from '@/components/work/workLayout'
import type { RoomTask } from '@/cx_types'
import type { PreviewLocate } from '@/lib/previewQuestion'
import type { TaskSource } from '@/types/taskOrigin'
import type { TaskOverviewHandle } from './topicView/actions'
import type { TaskForm } from './topicView/editorForm'
import type { ChatColumnHandle, WorkPanelHandle } from './topicView/shared'

import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, provide, ref, toRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { useQuery } from '@tanstack/vue-query'

import { provideAcceptCard } from '@/composables/useAcceptCard'
import { useChapters } from '@/composables/useChapters'
import { useConversationFiles } from '@/composables/useConversationFiles'
import { usePageTitle } from '@/composables/usePageTitle'
import { useScreenHeader } from '@/composables/useScreenHeader'
import { PANEL_OPEN_MIN } from '@/composables/useWorkspaceLayout'

import { useTopicActions } from './topicView/actions'
import { useTopicAddress } from './topicView/address'
import { useAdoptInPlace } from './topicView/adoptInPlace'
import { useCardPhase, useTopicConversation } from './topicView/conversation'
import { useDeliveryTab } from './topicView/delivery'
import { useSearchShortcut, useTaskForm } from './topicView/editorForm'
import { useTopicHeader } from './topicView/header'
import { useTopicLive } from './topicView/live'
import { useTopicPanelState } from './topicView/panel'
import { useReviewChat } from './topicView/reviewChat'
import { useSkillProposals } from './useSkillProposals'

import { newTask } from '@/commands/topicActions'
import HandInDialog from '@/components/accept/HandInDialog.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ChannelOverview from '@/components/channel/ChannelOverview.vue'
import NewThreadDialog from '@/components/channel/NewThreadDialog.vue'
import { roomTabs } from '@/components/panels/panelTabList'
import PanelThreads from '@/components/panels/PanelThreads.vue'
import PushPermissionPrompt from '@/components/PushPermissionPrompt.vue'
import DeliveryHead from '@/components/room/DeliveryHead.vue'
import SkillProposalCard from '@/components/room/SkillProposalCard.vue'
import TaskHeader from '@/components/task/TaskHeader.vue'
import TaskOverview from '@/components/task/TaskOverview.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import TopicHeader from '@/components/TopicHeader.vue'
import { TERMINAL_BLOCK } from '@/components/work/workLayout'
import WorkPanel from '@/components/WorkPanel.vue'
import { t } from '@/i18n'
import { usePagePanels } from '@/layouts/pagePanels'
import { PENDING_NOTES, usePendingNotes } from '@/lib/pendingNotes'
import { warmRoutesWhenIdle } from '@/lib/routePrefetch'
import { sourceRoute } from '@/lib/taskSource'
import { taskState } from '@/lib/taskState'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { VIEWING_CONTEXT } from '@/lib/viewingContext'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { openRoomTasksQuery, roomTaskQuery } from '@/query/room'
import { useWorkspaceStore } from '@/stores/workspace'
import { CHANNEL_OVERVIEW_FITS, CONVERSATION, type RoomWorkTab, useRoomLayout } from '@/views/workspace/roomLayout'
import TopicChatColumn from '@/views/workspace/TopicChatColumn.vue'
import { useChannelOverview } from '@/views/workspace/useChannelOverview'
import { useTaskOverview } from '@/views/workspace/useTaskOverview'

// 话题视图: ONE topic header, then the chat | 工作面板 split. A task in the room
// is drawn by the same view with the task's header, its own conversation in the
// chat column and its own 概览 / 现场 / 改动 / 预览 in the panel. The input bar is
// the chat column's own — it used to span both columns from here, which read as
// addressing the whole topic while 99% of what it sent was a chat message only
// the left column shows. Which topic is open is a route param, and ProjectShell
// keys this view on it: every topic gets a fresh instance, so nothing below — or in
// any child — can carry one topic's state into the next.
//
// 这一页自己只做三件事：认地址（`topicView/address.ts`）、把两侧的把手接起来
// （`panel.ts` / `actions.ts`）、把从地址和把手推出来的那几样交给模板。按职责分出去的
// 那几块各在自己的文件里，这一页只是它们碰头的地方——所以这里能看出来的也只有页面的
// 结构，没有具体的活。
defineOptions({ name: 'TopicView' })

// 支线那一半只在打开一条支线时才要，用到时再取。
const ThreadPane = defineAsyncComponent(() => import('@/views/workspace/ThreadPane.vue'))

// `taskId`：地址指着这个频道里的一个任务时，画的是任务页。`threadId`：指着频道里的一条
// 支线时，桌面上它在右栏「支线」那一格里，手机上是一整页。`reviewTaskId`：在频道里审阅
// 一件任务，桌面上是右栏里那一格交付页签，手机上是一整页。
const props = defineProps<{
  projectId: string
  topicId: string
  taskId?: string
  threadId?: string
  reviewTaskId?: string
}>()
// 地址此刻指着一条支线或一件交付：右栏一定开着，收起它就是离开那一处、回到频道。
const beside = computed(() => !!props.threadId || !!props.reviewTaskId)
const { mdAndUp } = useDisplay()
const router = useRouter()
const route = useRoute()
const store = useWorkspaceStore()

/** 这一页在说的那个地址：下面几块都从它取参数，都跟着它走。 */
const here = () => props

// ---- 地址：打开的是哪一页、哪一段，以及面板开着哪一格 ----
const address = useTopicAddress({ place: here, desktop: mdAndUp })
const {
  place: selectedTopic,
  resolving,
  redirecting,
  conversationId,
  unreadOnOpen,
  panelTab,
  onPanelTab,
  panelRoutineId,
  focusBlock,
  threadFocusBlock,
  backToRoom,
  openTopic,
  jumpTo,
  onReview,
  openPlace,
  markReadOnSwitch,
} = address

// 两块要往回够的组件（面板和对话栏）和它们的参数。面板收起再打开要回到原来那一格，
// 所以它自己手里那一样也得问得到（`showing`）。
const panesRef = ref<HTMLElement | null>(null)
const panelRef = ref<WorkPanelHandle | null>(null)
const chatColumn = ref<ChatColumnHandle | null>(null)
// 审阅那一栏的对话栏：按任务 id 各记一份，预览里指出的一处发进那一件任务自己的对话。
const reviewChat = useReviewChat()
const taskOverviewRef = ref<TaskOverviewHandle | null>(null)

// ---- 右侧面板的收 / 开（三档见 useTopicPanel）----
// 频道页和任务页「对话」那一种摆法（一块一块的）里，开没开只算这一页的：每进一个频道、一件任务
// 旁边都是概览（布局区那一份下面才建，建好后由它说这一页是不是那一种）。
const blockForm = ref(false)
const panelState = useTopicPanelState({
  scope: () => (blockForm.value ? props.taskId ?? 'channel' : null),
  // 频道页的概览是一栏窄的目录：放得下对话加这一栏就开着。任务页照默认的宽度。
  openMin: () => (props.taskId ? PANEL_OPEN_MIN : CHANNEL_OVERVIEW_FITS),
  panes: panesRef,
  tab: panelTab,
  desktop: mdAndUp,
  thread: beside,
  setTab: (tab) => void router.replace({ query: { ...route.query, tab } }),
  showing: () => panelRef.value?.activeTab(),
  backToRoom,
})
const {
  open: panelOpen,
  float: panelFloat,
  close: closePanel,
  toggle: togglePanel,
  closeLabel,
  show: panelShow,
} = panelState

// ---- 任务页的两种摆法：对话、编辑器（见 topicView/editorForm.ts 和 roomLayout.ts）----
const taskForm = useTaskForm({
  taskId: () => props.taskId,
  available: () => mdAndUp.value && !!props.taskId && !panelFloat.value,
})
/**
 * 换摆法。地址里的 `?tab=` 是对话那一种的：换到编辑器之前先拿掉它，编辑器开着哪几个页签是它自己
 * 记着的，不把对话那一种开着的概览带进来。
 */
async function chooseForm(form: TaskForm) {
  if (form === 'editor' && route.query.tab !== undefined)
    await router.replace({ query: { ...route.query, tab: undefined } })
  taskForm.choose(form)
}
// 编辑器里 `?tab=` 只是一个口子：地址点名的那一样（「审阅」、贴进来的链接）由面板开成一个页签，
// 地址随即拿掉它，下一次点名同一样时照样开。面板挂上之后才拿：它挂上时要先读到。
watch(
  [() => taskForm.editor.value, panelTab, () => !!panelRef.value],
  ([editor, tab, mounted]) => {
    if (!editor || tab === undefined || !mounted) return
    void nextTick(() => router.replace({ query: { ...route.query, tab: undefined } }))
  },
  { immediate: true }
)

// 右栏此刻开着哪一份文件：撰写框上「芝士会看到你在看什么」那一行读它
// （lib/viewingContext.ts）。面板收起时什么也没在看。
const viewingPath = ref<string | null>(null)
provide(
  VIEWING_CONTEXT,
  computed(() =>
    viewingPath.value && (!mdAndUp.value || panelOpen.value)
      ? { kind: 'viewing' as const, path: viewingPath.value, task_id: props.taskId ?? null }
      : null
  )
)
// 「待发批注」：右栏预览往里攒，对话输入框上方逐条列出来，一起发。整个话题共一份。
// 没有对话栏的场合（资料库、产物页那些面）预览退回自己面板底下那条。
const pendingNotes = usePendingNotes((content) => chatColumn.value?.say(content) ?? false)
provide(PENDING_NOTES, pendingNotes)

// 这个话题（任务）的采纳卡，整页一份：对话栏的那一条、「改动」页顶部、对话看不见时的那一条和手机
// 上面板底部那一条读的都是它（见 provideAcceptCard）。
const accept = provideAcceptCard({
  get topicId() {
    return props.topicId
  },
  get topicStatus() {
    return selectedTopic.value?.status ?? ''
  },
  get taskId() {
    return props.taskId ?? null
  },
  get projectId() {
    return props.projectId
  },
})
function reloadAccept() {
  void accept.reload()
}
// 采纳卡处在哪一段：面板要按它挑页签，所以这一个词往上走。
const cardPhase = useCardPhase(accept)

// ---- 这一段对话：支线、任务页、署名（见 topicView/conversation.ts）----
const conversation = useTopicConversation({
  place: here,
  room: () => selectedTopic.value,
  onPlaceSwitch: markReadOnSwitch,
})
const {
  channelThreads,
  showThread,
  onOpenThread,
  openTaskPage,
  openTask,
  showAsTask,
  upgradeMessage,
  upgradeInColumn,
  newThreadOpen,
  creatingThread,
  canOpenThread,
  createThread,
  canRestoreThread,
  restoreAThread,
  threadTime,
  AUTHOR,
  taskPage,
  currentTask,
  taskLoading,
  taskLoadError,
  taskOwner,
  taskOpen,
  composerClosed,
  askClosed,
  roomAgents,
  memberNames,
} = conversation

// ---- 这个房间此刻是不是活的（见 topicView/live.ts）----
// 对话里 AI 队友提议的技能：卡片点开在右侧那一格读完再决定（WorkPanel 的 `#skill`），
// 取数和保存在这一页。卡片属于提出它的那段对话。第一次点开一张卡才去读，读过之后
// 换对话、提议有变动再跟着读——没人点开就不为它多发一次请求。
const skillConversation = computed(() => props.taskId ?? props.threadId ?? props.topicId)
const skills = useSkillProposals(
  toRef(() => props.projectId),
  skillConversation
)
const skillsRead = ref(false)
function loadSkills() {
  skillsRead.value = true
  return skills.load()
}
watch(skillConversation, () => {
  if (skillsRead.value) void skills.load()
})
// 地址直接指着右侧那一格技能（刷新、发出去的链接）：那一格要画它，这时也要读。
watch(
  () => panelTab.value,
  (tab) => {
    if (!skillsRead.value && tab?.startsWith('file:skill:')) void loadSkills()
  },
  { immediate: true }
)
function openSkillPage(skill: { id: string }) {
  void router.push({ name: 'project-skills', params: { projectId: props.projectId }, query: { skill: skill.id } })
}

const live = useTopicLive({
  conversationId: () => conversationId.value,
  taskId: () => props.taskId,
  reloadTask: () => taskPage.load(true),
  noteChatNews: () => room.noteChatNews(),
  reloadAccept,
  reloadSkills: () => {
    if (skillsRead.value) void skills.load()
  },
  chat: () => chatColumn.value,
})
const { working, activity, agentControl, siteTurns, activityTick, roomConnected } = live
const { handleWorking, handleTurnDone, handleStateChanged } = live

// ---- 页头和这一页的标题（见 topicView/header.ts）----
const { taskHeaderBinding, topicHeaderBinding } = useTopicHeader({
  place: here,
  room: () => selectedTopic.value,
  task: taskPage.task,
  taskHeaderProps: taskPage.headerProps,
  memberNames,
  roomAgents,
  roomConnected,
  panelOpen,
  backToRoom,
  togglePanel,
  openTopic,
  me: AUTHOR,
  handIn: accept.handIn,
  // 布局区那一份在下面才建（它要读打开的交付）；页头渲染时早已有了。
  resetLayout: () => (room.panels.rearranged.value ? room.panels.reset : null),
  panelControls: () => room.panelControls.value,
  channelBlocks: () => channelBlocks.value,
  toggleChannelBlock: (key) => toggleChannelBlock(key),
  form: () => (room.workLayout.value && props.taskId && !panelFloat.value ? taskForm.chosen.value : null),
  chooseForm: (form) => void chooseForm(form),
  editorParts: () => editorParts.value,
  toggleEditorPart,
  blocks: () => taskBlocks.value,
  toggleBlock,
})
/**
 * 页头上那几颗按钮，面板是浮层（或没交给外壳）时：点的那一样正显示着就收起面板；否则把面板打开
 * 在它上面。打开时它就是正显示着的那一样的话只打开，不再写一遍地址（收起前在看什么，打开还是什么）。
 */
function togglePlace(key: string) {
  const panel = panelRef.value
  const showing = panel?.activeTab?.()
  if (panelOpen.value && showing === key) return closePanel()
  if (!panelOpen.value) panelShow()
  if (showing !== key) panel?.select?.(key)
}
// ---- 在频道里审阅的那件交付、右栏开着哪一格（见 topicView/delivery.ts）----
const reviewReload = ref(0)
/** 布局区那一份（下面才建）：频道页右边是不是一块一块的。建好之前不是。 */
let roomLayoutHandle: { workLayout: { value: { blocks?: boolean } | null } } | null = null
const delivery = useDeliveryTab({
  place: here,
  desktop: mdAndUp,
  urlTab: panelTab,
  panelOpen,
  activeTab: () => panelRef.value?.activeTab(),
  me: () => myHandle(),
  readOpenTasks: async () =>
    (await queryClient.fetchQuery({ ...openRoomTasksQuery(props.topicId), staleTime: 0 })).data,
  openDelivery: (taskId) => void openTask(taskId),
  setTab: onPanelTab,
  toChannelTab: (key) =>
    void router.push({
      name: 'workspace-topic',
      params: { projectId: props.projectId, topicId: props.topicId },
      query: { tab: key },
    }),
  refreshDelivery: (taskId) => {
    reviewReload.value += 1
    void queryClient.invalidateQueries({ queryKey: keys.roomTask(taskId) })
  },
  blocks: () => !props.taskId && roomLayoutHandle?.workLayout.value?.blocks === true,
})
const {
  tabs: deliveryTabs,
  shownTab,
  select: onWorkPanelTab,
  close: closeDelivery,
  arrived: onDeliveryArrived,
} = delivery
const deliveryTaskId = computed(() => props.reviewTaskId ?? null)
const reviewRead = useQuery(
  computed(() => ({ ...roomTaskQuery(deliveryTaskId.value ?? ''), enabled: !!deliveryTaskId.value })),
  queryClient
)
const reviewTask = computed<RoomTask | null>(() => (deliveryTaskId.value ? reviewRead.data.value ?? null : null))
const reviewHead = computed(() => ({ heading: reviewTask.value ? taskTitle(reviewTask.value) : '' }))
// 页头上的状态词和在等谁：和主线标签、侧栏任务行同一份算法（`lib/taskState`）。
function stateOf(task: RoomTask | null) {
  return task
    ? taskState(task, {
        viewer: myHandle(),
        nameOf: (handle: string) => memberNames.value[handle] || handle,
        agentName: store.agentName,
      })
    : null
}
const reviewState = computed(() => stateOf(reviewTask.value))
// 右栏里开着的每一格任务各读各的（页签上的名字、它那一栏的页头和只读与否）。
// 只在页签打开时读一次（同一份缓存，`roomTaskQuery`）；正看着的那一格读的是上面那份活的。
const tabTasks = ref<Record<string, RoomTask>>({})
watch(
  () => deliveryTabs.value.map((tab) => tab.taskId),
  (ids) => {
    for (const id of ids) {
      if (tabTasks.value[id]) continue
      void queryClient
        .fetchQuery(roomTaskQuery(id))
        .then((task) => (tabTasks.value = { ...tabTasks.value, [id]: task }))
        .catch(() => undefined)
    }
  },
  { immediate: true }
)
function tabTask(taskId: string): RoomTask | null {
  return tabTasks.value[taskId] ?? null
}
/** 这件任务读回来的那一行：在它的地址上是 `reviewTask`，别的开着的页签各读各的。 */
function taskFor(taskId: string): RoomTask | null {
  return taskId === deliveryTaskId.value ? reviewTask.value : tabTask(taskId)
}
// 只在频道页上：任务页上没有在频道里打开的任务这回事。
const deliveryTabsBinding = computed(() =>
  mdAndUp.value && !props.taskId
    ? deliveryTabs.value.map((tab) => {
        const task = taskFor(tab.taskId)
        return { id: tab.taskId, title: task ? taskTitle(task) : '', dot: tab.dot }
      })
    : []
)

// ---- 桌面：对话和工作面板的每一格都是外壳布局区里的一块（见 roomLayout.ts）----
// 这一页自己那一格只剩页头。频道页、任务页各记各的摆法。面板收着、或主区太窄成了浮层时，
// 只装着工作面板的那几格藏起来；浮层那一档面板自己排页签。
const docked = computed(() => !panelFloat.value && panelOpen.value)
const roomReady = computed(() => !!selectedTopic.value && !(props.taskId && !currentTask.value))
type WorkPanelMeta = WorkPanelHandle & {
  fixedMeta?: WorkTabMeta[]
  fileMeta?: WorkTabMeta[]
  terminalMeta?: WorkTabMeta[]
}
/** 工作面板固定的几格：面板算好的名字和信号，没挂上时照表。 */
function fixedTabs(): WorkTabMeta[] {
  return (
    (panelRef.value as WorkPanelMeta | null)?.fixedMeta ??
    roomTabs({ withChat: false, review: false, task: !!props.taskId }).map((tab) => ({
      key: tab.key,
      title: tab.label,
      icon: tab.icon,
    }))
  )
}
/** 工作面板此刻有哪几格：固定的几格，打开的任务，打开的文件。 */
function workTabs(): RoomWorkTab[] {
  const handle = panelRef.value as WorkPanelMeta | null
  return [
    ...fixedTabs().map((tab) => ({ ...tab, alwaysRender: tab.key === 'preview' })),
    ...deliveryTabsBinding.value.map((tab) => ({
      key: `delivery:${tab.id}`,
      title: tab.title || t('work.room.delivery.title'),
      icon: 'mdi-file-eye-outline',
      signal: tab.dot ? ({ kind: 'dot' } as const) : undefined,
      transient: true,
      alwaysRender: true,
    })),
    ...(handle?.fileMeta ?? []).map((tab) => ({ ...tab, transient: true, alwaysRender: true })),
    // 终端：「+」里那一项，和读者开着的每一格（一格一个 shell，挪动时不能重画）。
    ...(handle?.terminalMeta ?? []).map((tab) => ({ ...tab, transient: !tab.spawns, alwaysRender: true })),
  ]
}
const room = useRoomLayout(usePagePanels, {
  kind: () => (mdAndUp.value ? (props.taskId ? (taskForm.editor.value ? 'editor' : 'task') : 'room') : null),
  tabs: workTabs,
  // 编辑器里各块藏不藏是它自己的（页头右边那三颗），不跟着对话那一种的面板收没收。
  workHidden: () => !roomReady.value || (!taskForm.editor.value && !docked.value),
  floating: () => panelFloat.value,
  hide: { label: () => closeLabel.value, run: () => closePanel() },
  onActivated: (key) => (panelRef.value as { activated?: (key: string) => void } | null)?.activated?.(key),
  onClose: (key) => {
    panelRef.value?.closeTab?.(key)
    leavePlaceOf(key)
  },
  onDrop: (key) => panelRef.value?.closeTab?.(key),
  onKeep: (key) => (panelRef.value as { keepTab?: (key: string) => void } | null)?.keepTab?.(key),
  onSpawn: (key) => void (panelRef.value as { spawnTab?: (key: string) => void } | null)?.spawnTab?.(key),
  scope: () => props.taskId,
})
roomLayoutHandle = room
watch(
  () => !!room.workLayout.value?.blocks,
  (blocks) => (blockForm.value = blocks),
  { immediate: true }
)
/**
 * 频道页上关掉了地址指着的那一块（一条支线、在频道里打开的那件任务）：地址回到频道，别的那几块
 * 照样开着。
 */
function leavePlaceOf(key: string) {
  if (props.taskId) return
  const named =
    (key === 'threads' && props.threadId) || (props.reviewTaskId && key === `delivery:${props.reviewTaskId}`)
  if (named)
    void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId: props.topicId } })
}
/** 打开的一条支线、一件任务换了（点了「N 条回复」、在频道里打开一件任务）：它那一块打开在它上面。 */
watch(
  () => [props.threadId, props.reviewTaskId],
  ([thread, review]) => {
    if (!room.workLayout.value?.blocks || props.taskId || !(thread || review)) return
    panelShow()
    const key = shownTab.value
    if (key) panelRef.value?.select?.(key)
  }
)
/**
 * 频道页页头右边那一组：概览、支线、文件，例行事务收在 ⋯ 里。一块一块的那一种里开着的那几颗是
 * 按下的样子（「文件」那一块显示一份文件时也算开着）；浮层和没交给外壳时，按下的是正显示的那一样。
 */
const channelBlocks = computed<TaskBlockButton[]>(() => {
  if (props.taskId) return []
  const meta = new Map(fixedTabs().map((tab) => [tab.key, tab]))
  const shown = room.chatBlocks.value
  const showing = panelOpen.value ? panelRef.value?.activeTab?.() ?? null : null
  const inBlocks = !!room.workLayout.value?.blocks
  const on = (key: string) => (inBlocks ? openBlockOf(key, shown) !== null : showing === key)
  const button = (key: string, more = false): TaskBlockButton[] => {
    const tab = meta.get(key)
    if (!tab) return []
    return [
      { key, label: tab.title, hint: tab.hint, icon: tab.icon, on: on(key), more, dot: tab.signal?.kind === 'dot' },
    ]
  }
  return [...button('overview'), ...button('threads'), ...button('files'), ...button('routines', true)]
})
/** 页头上的一颗管着的那一块此刻开着的是哪一样（「文件」那一颗管着打开的文件）；没开着是 null。 */
function openBlockOf(key: string, shown: { overview: boolean; open: readonly string[] }): string | null {
  if (key === 'overview') return shown.overview ? 'overview' : null
  if (key === 'files') return shown.open.find((one) => one === 'files' || one.startsWith('file:')) ?? null
  return shown.open.includes(key) ? key : null
}
/** 页头那一组里点了一颗：开着的那一块关掉；关着的打开（概览让开）。浮层里照旧一次一样。 */
function toggleChannelBlock(key: string) {
  if (!room.workLayout.value?.blocks) return togglePlace(key)
  const open = openBlockOf(key, room.chatBlocks.value)
  if (open) return room.closeBlock(open)
  if (!panelOpen.value) panelShow()
  panelRef.value?.select?.(key)
}
/** 概览里「全部 N 项」：打开那一块的列表（「支线」那一块正显示一条支线时回到列表）。 */
function showList(key: 'threads' | 'files' | 'routines') {
  if (key === 'threads' && props.threadId) return backToThreads()
  panelRef.value?.select?.(key)
}
/** 概览里点了一条例行事务：「例行事务」那一块打开，选中它。 */
function openRoutine(id: string) {
  void router.replace({ query: { ...route.query, routine: id } })
  panelRef.value?.select?.('routines')
}
/**
 * 桌面上概览里点了一件任务：在频道里打开它（「支线」那一处）。地址指着一条支线时也一样：概览是
 * 频道的，不是那条支线的。
 */
function openFromOverview(taskId: string) {
  reviewView.value = null
  returnAsk.value = null
  void router.push({
    name: 'workspace-review',
    params: { projectId: props.projectId, topicId: props.topicId, reviewTaskId: taskId },
  })
}
/** 概览里点了一份文件：在「文件」那一块里打开它。 */
function openOverviewFile(row: { kind: 'file' | 'document'; id: string; name: string }) {
  if (row.kind === 'document') void panelRef.value?.openDocument?.({ id: row.id, title: row.name })
  else panelRef.value?.openFile?.(row.id)
}
// 这个频道里的文件：概览里列着最近几份，「文件」那一块读的也是这一份。
const roomFiles = useConversationFiles(computed(() => (props.taskId ? null : props.topicId)))
/**
 * 「对话」那一种摆法里页头右边那一组：概览、改动（带改过几个文件）、文件、终端（能开终端时），
 * 现场和预览收在 ⋯ 里（预览只在有东西可看、或它那一块开着时）。
 */
const taskBlocks = computed<TaskBlockButton[]>(() => {
  if (!props.taskId) return []
  // 编辑器里是 ⋯ 里的两项：在中间打开概览、预览。
  if (taskForm.editor.value) {
    return fixedTabs()
      .filter((tab) => tab.key === 'overview' || tab.key === 'preview')
      .map((tab) => ({ key: tab.key, label: tab.title, icon: tab.icon, on: false, more: true }))
  }
  const shown = room.chatBlocks.value
  const on = (key: string) => (key === 'overview' ? shown.overview : shown.open.includes(key))
  const meta = new Map(fixedTabs().map((tab) => [tab.key, tab]))
  const button = (key: string, more = false): TaskBlockButton[] => {
    const tab = meta.get(key)
    if (!tab) return []
    const count = key === 'changes' && tab.signal?.kind === 'count' ? tab.signal.count : 0
    return [{ key, label: tab.title, hint: tab.hint, icon: tab.icon, on: on(key), count, more }]
  }
  const terminals = workTabs().some((tab) => tab.terminal)
  const preview = meta.get('preview')
  return [
    ...button('overview'),
    ...button('changes'),
    ...button('code'),
    ...(terminals
      ? [{ key: TERMINAL_BLOCK, label: t('work.room.tabs.terminal'), icon: 'mdi-console', on: on(TERMINAL_BLOCK) }]
      : []),
    ...button('site', true),
    ...(preview && (!preview.faint || on('preview')) ? button('preview', true) : []),
  ]
})
/** 页头上那一组里点了一颗：开着的那一块关掉；关着的打开（工作面板那一侧收着时先打开它）。 */
function toggleBlock(key: string) {
  if (taskForm.editor.value) return panelRef.value?.select?.(key)
  const shown = room.chatBlocks.value
  if (key === 'overview' ? shown.overview : shown.open.includes(key)) return room.closeBlock(key)
  if (!panelOpen.value) panelShow()
  panelRef.value?.select?.(key)
}
/** 一块标题栏上的「在编辑器里打开」：换到编辑器，翻到那一格（那份文件、那个终端）。 */
async function openInEditor(key?: string) {
  await chooseForm('editor')
  if (!key) return
  if (key.startsWith('terminal:') && !room.editorParts.terminal) room.toggleEditorPart('terminal')
  // 编辑器那一种摆法要先交给外壳、摆出来，才翻得到。
  await nextTick()
  await nextTick()
  panelRef.value?.select?.(key)
}
// Ctrl+Shift+F：搜这件任务的文件。「对话」那一种先换到编辑器，左栏藏着就摆出来，再翻到「搜索」。
useSearchShortcut({
  enabled: () => !!props.taskId && mdAndUp.value && !panelFloat.value,
  run: async () => {
    if (!taskForm.editor.value) await chooseForm('editor')
    if (!room.editorParts.left) room.toggleEditorPart('left')
    // 编辑器那一种摆法要先交给外壳、摆出来，左栏才在。
    await nextTick()
    await nextTick()
    panelRef.value?.searchFiles?.()
  },
})
/** 编辑器里页头右边那三颗按下没有。 */
const editorParts = computed(() => ({ ...room.editorParts }))
/** 页头上藏起或摆回一块。摆回下面那一块时一个终端都没开（而能开）的话，开一个。 */
function toggleEditorPart(part: 'left' | 'terminal' | 'conversation') {
  room.toggleEditorPart(part)
  if (part !== 'terminal' || !room.editorParts.terminal) return
  const tabs = workTabs()
  const canOpen = tabs.some((tab) => tab.terminal && tab.spawns)
  const anyTerminal = tabs.some((tab) => tab.terminal && !tab.spawns)
  if (canOpen && !anyTerminal)
    void (panelRef.value as { spawnTab?: (key: string) => void } | null)?.spawnTab?.('terminal')
}
const conversationTarget = computed(() => room.panels.outlet(CONVERSATION))
/** 对话那一格里的那一块：窄档的面板浮层就压在它上面。 */
const conversationBox = ref<HTMLElement | null>(null)
/** 对话看不见（被放大的那一格盖住、或收着）：采纳那一条贴到读者在看的那一格工作面板底下。 */
const acceptElsewhere = computed(() => !room.conversationInSight.value)
// 打开的这件任务看哪一种（对话 / 改动 / 预览）：主线上「查看改动」「预览」点名，人在
// 那一栏里换了就跟着换。记着是哪件任务的：换一件任务从它的对话看起。
const reviewView = ref<{ taskId: string; view: string } | null>(null)
// 主线上按了「退回」：这件任务的页签开出写理由的那一块。每按一次一个新记号，只给那件任务。
const returnAsk = ref<{ taskId: string; token: string } | null>(null)
let returnAsks = 0
function openTaskChat(taskId: string) {
  reviewView.value = null
  returnAsk.value = null
  return openTask(taskId)
}
// 主线上交付那几颗按钮：去看改动、预览，就地采纳，或退回（topicView/adoptInPlace.ts）。
const adoptInPlace = useAdoptInPlace({
  me: () => myHandle(),
  store,
  topicId: () => props.topicId,
  openTask: (taskId) => openTask(taskId),
  showView: (taskId, view) => {
    reviewView.value = { taskId, view }
    returnAsk.value = null
  },
  askReturn: (taskId) => (returnAsk.value = { taskId, token: `${taskId}:${++returnAsks}` }),
})
const adoptingTask = adoptInPlace.adopting
// 审阅那一栏（工作面板的审阅模式）要的几样：这件任务的对话、改动和预览。
function reviewPanel(taskId: string) {
  return {
    review: true,
    reviewReload: reviewReload.value,
    topic: selectedTopic.value,
    taskId,
    taskReadOnly: !taskFor(taskId) || taskFor(taskId)?.status !== 'open',
    taskHasBranch: taskFor(taskId)?.branch_name !== null,
    activityTick: activityTick.value,
    agentName: store.agentName,
    agentHandle: taskFor(taskId)?.agent_handle ?? store.agentHandle,
    members: store.members,
    memberNames: memberNames.value,
    topicList: store.topics,
    tab: reviewView.value?.taskId === taskId ? reviewView.value.view : undefined,
    askReturn: returnAsk.value?.taskId === taskId ? returnAsk.value.token : null,
    // 预览里指一处、画完标注发出去：进这件任务自己的对话，不进频道主对话。指一处走
    // `@locate`（下面模板里接），结构化提问走这几个 props；两样都落在同一份对话
    // 句柄上（见 `topicView/reviewChat.ts`）——频道的发送口是另一个实例，两者不能混。
    submitQuestion: reviewChat.submit(taskId),
    // `v-bind` 摊开的对象不 unref，`askPersonal` 是 computed：这里取它此刻的值，
    // 不然面板拿到的是一只 ref，按下去不是调用而是「ref 不可调用」。
    askPersonal: askPersonal.value,
  }
}
// 支线左上角的返回：回到「支线」那一格的列表。
function backToThreads() {
  void router.push({
    name: 'workspace-topic',
    params: { projectId: props.projectId, topicId: props.topicId },
    query: { tab: 'threads' },
  })
}
// 这一页的标题是在审阅的那件任务（频道主线还在旁边，审阅是这一页正在做的事）。
const pageTitle = usePageTitle()
watch(
  reviewHead,
  ({ heading }) => {
    if (heading) pageTitle.setDynamicTitle(heading, 'workspace-review')
    else pageTitle.clearDynamicTitle('workspace-review')
  },
  { immediate: true }
)
onUnmounted(() => pageTitle.clearDynamicTitle('workspace-review'))

useScreenHeader(() => {
  if (!mdAndUp.value && props.reviewTaskId) {
    const reviewing = props.reviewTaskId
    return {
      content: {
        component: DeliveryHead,
        props: { ...reviewHead.value, state: reviewState.value, bar: true, onOpenTask: () => openTaskPage(reviewing) },
      },
    }
  }
  if (mdAndUp.value || beside.value) return null
  const [component, binding] = props.taskId
    ? [TaskHeader, taskHeaderBinding.value]
    : [TopicHeader, topicHeaderBinding.value]
  return binding ? { content: { component, props: binding } } : null
})

// ---- 频道概览：置顶、任务、综合的项目总览 ----
const channelOverview = useChannelOverview({
  channelId: () => (props.taskId ? null : props.topicId),
  projectId: () => props.projectId,
  general: () => selectedTopic.value?.kind === 'root',
  tick: () => activityTick.value,
  reportError: (e) => store.reportError(e, t('work.channel.pins.unpinFailed')),
})
const canPin = computed(() => {
  const topic = selectedTopic.value
  return !!topic && topic.status !== 'archived' && (topic.kind === 'root' || !!topic.joined)
})

// ---- 章节：这段对话（频道主线或任务）的目录 ----
// 跟对话走：频道是频道本身，任务页是任务的 id。任务的对话里项目的人都能说话，所以
// 都能整理章节；频道主线仍然要加入了这个频道。
const chapters = useChapters({
  conversationId: () => conversationId.value,
  report: (message) => store.reportError(new Error(message), t('work.channel.chapters.unpinFailed')),
})
const canChapter = computed(() => {
  const topic = selectedTopic.value
  if (!topic || topic.status === 'archived') return false
  return !!props.taskId || topic.kind === 'root' || !!topic.joined
})

// 任务概览那一列要的：相关、这一轮的清单、第一轮失败后的重试。
const taskOverview = useTaskOverview({
  taskId: () => props.taskId,
  working: () => working.value,
  reloadTask: () => taskPage.load(true),
})
// 任务页顶上那句话：回到它所在的那段对话（频道主线、支线或另一个任务），停在它上面。
function openSource(source: TaskSource) {
  if (source.where === 'thread') channelThreads.markSeen(source.conversation_id)
  void router.push(sourceRoute(props.projectId, source))
}

// 对话栏和面板回上来说的那几件事，在这一页各归哪一处（见 topicView/actions.ts）。
const { chatEvents, onLocate, submitQuestion, askPersonal, handleMentionClick } = useTopicActions({
  place: here,
  panel: { show: panelShow },
  onPanelTab: onWorkPanelTab,
  onReview,
  reloadAccept,
  loadSkills,
  opening: { onOpenThread, openTask: openTaskChat, upgradeInColumn },
  openTopic,
  panelRef,
  chatColumn,
  taskOverviewRef,
  activity,
  agentControl,
  siteTurns,
  handleTurnDone,
  handleWorking,
  handleStateChanged,
  onDelivery: (act) => void adoptInPlace.onDelivery(act),
})

// 话题画出来之后，趁浏览器空着把从这里最常去的几页的代码先下下来：总览、资料库、
// 项目文档、搜索。点过去时就只剩取数据那一段等待（lib/routePrefetch.ts）。
let cancelRouteWarm: (() => void) | null = null
onMounted(() => {
  const params = { projectId: props.projectId }
  cancelRouteWarm = warmRoutesWhenIdle(router, [
    { name: 'workspace-overview', params },
    { name: 'project-library', params },
    { name: 'project-docs', params: { ...params, kind: 'charter' } },
    { name: 'project-search', params },
  ])
})
onUnmounted(() => cancelRouteWarm?.())

void openPlace()
</script>

<template>
  <div class="topic-view d-flex flex-column fill-height" style="min-width: 0">
    <!-- 桌面上它在对话那一格里（页面这一格只放页头）。 -->
    <Teleport v-if="!selectedTopic" :to="conversationTarget" :disabled="!conversationTarget">
      <div class="room-state flex-grow-1 d-flex align-center justify-center">
        <v-progress-circular v-if="resolving || redirecting" indeterminate color="primary" />
        <div v-else class="text-center">
          <div class="t-body c-muted">{{ t('work.topic.notFound') }}</div>
          <div class="t-meta mt-1">{{ t('work.topic.notFoundHint') }}</div>
        </div>
      </div>
    </Teleport>

    <template v-else>
      <!-- Screen-reader heading for the room. Text from `topicTitle`, the same
           source as the `workspace-topic` dynamic title the top bar reads. The
           room name is drawn as a span in TopicHeader (not a heading); the
           project shell adds its own h1 for the project. Hidden: the name is
           already on screen. -->
      <h1 class="visually-hidden">
        {{ taskId && currentTask ? taskTitle(currentTask) : topicTitle(selectedTopic) }}
      </h1>
      <!-- 一条话题头部，横跨对话和工作面板。任务页上是任务的那一条：同一个高度，
           写着它在哪个房间、谁负责。 -->
      <!-- 手机上这一行是顶栏的内容（script 里的 useScreenHeader；支线页见 ThreadPane）。 -->
      <template v-if="mdAndUp">
        <template v-if="taskId">
          <TaskHeader v-if="taskHeaderBinding" v-bind="taskHeaderBinding" />
        </template>
        <TopicHeader v-else-if="topicHeaderBinding" v-bind="topicHeaderBinding" />
      </template>
      <!-- The hand-in panel the task header's submit button opens, on both widths. -->
      <HandInDialog
        v-if="taskId"
        :model-value="accept.handIn.open.value"
        :again="accept.handIn.again.value"
        :adopts="accept.handIn.adopts.value"
        :files="accept.handIn.files.value"
        :subject="accept.handIn.subject.value"
        :body="accept.handIn.body.value"
        :reviewer="accept.handIn.reviewer.value"
        :reviewers="accept.handInReviewers.value"
        :drafting="accept.handIn.drafting.value"
        :submitting="accept.handIn.submitting.value"
        :ready="accept.handIn.ready.value"
        @update:model-value="(open: boolean) => !open && accept.handIn.closePanel()"
        @update:subject="accept.handIn.touchSubject"
        @update:body="accept.handIn.touchBody"
        @update:reviewer="(value: string | null) => (accept.handIn.reviewer.value = value)"
        @submit="accept.handIn.submit()"
      />

      <!-- 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一刻。它自己决定
         什么时候出现（这一轮跑过一分钟、而且这个浏览器还没问过），平常什么都不
         画。放在这里而不是首屏：见组件自己的说明。 -->
      <PushPermissionPrompt :working="working" />

      <Teleport v-if="taskId && !currentTask" :to="conversationTarget" :disabled="!conversationTarget">
        <div class="task-state room-state flex-grow-1">
          <v-progress-circular v-if="taskLoading" indeterminate color="primary" size="24" />
          <span v-else class="t-body c-muted">{{ taskLoadError ?? t('work.task.notFound') }}</span>
        </div>
      </Teleport>
      <!-- 手机：支线是一整页，← 回到频道。 -->
      <ThreadPane
        v-else-if="threadId && !mdAndUp"
        class="flex-grow-1"
        page
        :room="selectedTopic"
        :thread-id="threadId"
        :focus-block="threadFocusBlock"
        :members="store.members"
        :topic-list="store.topics"
        :member-names="memberNames"
        :to-task="upgradeMessage"
        @close="backToRoom"
        @open-task="openTaskPage"
        @as-task="showAsTask"
        @open-file="(path: string) => panelRef?.openFile?.(path)"
        @open-topic="openTopic"
        @mention-click="handleMentionClick"
      />
      <!-- Phone: reviewing a task is a whole page, ← back to the channel; its head is the top bar's content. -->
      <WorkPanel
        v-else-if="reviewTaskId && !mdAndUp"
        :key="reviewTaskId"
        class="flex-grow-1"
        v-bind="reviewPanel(reviewTaskId)"
        @open-topic="openTopic"
        @open-card="openTaskPage"
        @mention-click="handleMentionClick"
        @navigate="router.push($event)"
        @update:tab="(key: string) => (reviewView = { taskId: reviewTaskId!, view: key })"
        @locate="(payload: PreviewLocate) => reviewChat.say(reviewTaskId!, payload)"
        @return-asked="returnAsk = null"
      >
        <template #chat>
          <TopicChatColumn
            :ref="(handle: unknown) => reviewChat.hold(reviewTaskId!, handle)"
            class="col col-chat flex-grow-1"
            beside
            :topic="selectedTopic"
            :task-id="reviewTaskId"
            :task-agent-handle="reviewTask?.agent_handle ?? null"
            :members="store.members"
            :topic-list="store.topics"
            :member-names="memberNames"
            :composer-closed="composerClosed"
            :ask-closed="askClosed || reviewTask?.status === 'closed'"
            @open-file="(path: string) => panelRef?.openFile?.(path)"
            @open-topic="openTopic"
            @open-card="openTask"
            @mention-click="handleMentionClick"
          />
        </template>
      </WorkPanel>
      <div
        v-else
        ref="panesRef"
        :key="taskId ?? 'room'"
        class="panes d-flex flex-grow-1"
        style="min-width: 0; min-height: 0; position: relative"
      >
        <!-- 桌面：对话是外壳布局区里的一格（传送进去；没有外壳时就在这一排里）。面板
             并排时在它右边那一格，窄档里是压在这一块上的浮层。 -->
        <Teleport v-if="mdAndUp" :to="conversationTarget" :disabled="!conversationTarget">
          <div ref="conversationBox" class="col room-conversation">
            <TopicChatColumn
              ref="chatColumn"
              class="col col-chat"
              :topic="selectedTopic"
              :members="store.members"
              :topic-list="store.topics"
              :unread-on-open="unreadOnOpen"
              :focus-block="focusBlock"
              :task-id="taskId ?? null"
              :task-agent-handle="currentTask?.agent_handle ?? null"
              :member-names="memberNames"
              :composer-closed="composerClosed"
              :ask-closed="askClosed"
              :accept-elsewhere="acceptElsewhere"
              :accept-review-alongside="taskForm.editor.value"
              :chapters="chapters.rows.value"
              :chapterable="canChapter"
              :chapter-ids="chapters.chapterIds.value"
              :adopting-task="adoptingTask"
              v-on="chatEvents"
              @chapter="chapters.add"
              @unchapter="chapters.remove"
              @rename="chapters.rename"
              @open-room="backToRoom"
              @delivery-arrived="onDeliveryArrived"
            />
            <!-- 面板浮层背后的遮罩。点它收起面板——和 Esc 同一条路。 -->
            <Transition name="panel-scrim">
              <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- Esc closes the panel too -->
              <div v-if="panelFloat && panelOpen" class="panel-scrim" @click="closePanel" />
            </Transition>
          </div>
        </Teleport>

        <!-- 工作面板：桌面上它的每一格各自传送进布局区里自己的那一块（WorkPanel 的 `layout`），
             这一层留在原地、不占地方；窄档里它整个是从右边拉进对话那一块的浮层（三档见
             useTopicPanel），浮层收起时 visibility 一并藏掉，不进 tab 序和读屏的树。手机上它就在
             这一排里，对话是它的第一个页签。 -->
        <Teleport :to="conversationBox ?? undefined" :disabled="!mdAndUp || !panelFloat || !conversationBox">
          <div
            v-show="!mdAndUp || panelFloat || panelOpen || !!room.workLayout.value"
            id="topic-panel"
            class="col col-doc panel-host"
            :class="{
              'panel-host--sheet': panelFloat,
              'panel-host--open': panelFloat && panelOpen,
              'panel-host--cells': !!room.workLayout.value,
            }"
            :style="panelFloat ? undefined : { flex: '1 1 0', minWidth: 0 }"
          >
            <WorkPanel
              ref="panelRef"
              :submit-question="submitQuestion"
              :ask-personal="askPersonal"
              :agent-name="store.agentName"
              :agent-handle="store.agentHandle"
              :members="store.members"
              :activity="activity"
              :topic="selectedTopic"
              :task-id="taskId ?? null"
              :task-read-only="!!taskId && (!taskOwner || !taskOpen)"
              :task-has-branch="currentTask?.branch_name !== null"
              :activity-tick="activityTick"
              :working="working"
              :agent-control="agentControl"
              :site-turns="siteTurns"
              :topic-list="store.topics"
              :tab="shownTab"
              :deliveries="deliveryTabsBinding"
              :routine-id="panelRoutineId"
              :card-phase="cardPhase"
              :with-chat="!mdAndUp"
              :compact="panelFloat || !panelOpen"
              :member-names="memberNames"
              :threads-new="channelThreads.hasNew.value"
              :layout="room.workLayout.value"
              :thread-open="!!threadId && mdAndUp"
              :room-files="taskId ? null : roomFiles"
              @open-topic="openTopic"
              @open-card="openTaskPage"
              @mention-click="handleMentionClick"
              @update:tab="onWorkPanelTab"
              @close-delivery="closeDelivery"
              @viewing="viewingPath = $event"
              @locate="onLocate"
              @navigate="router.push($event)"
              @open-editor="openInEditor"
              @hide-bottom="room.editorParts.terminal && room.toggleEditorPart('terminal')"
            >
              <!-- 对话看不见时，采纳那一条贴在读者在看的那一格底下：要做的决定不能跟着消失。 -->
              <template v-if="mdAndUp && acceptElsewhere && selectedTopic" #dock>
                <TopicAcceptCard
                  class="panel-accept"
                  :topic-id="selectedTopic.id"
                  :task-id="taskId ?? undefined"
                  :topic-status="selectedTopic.status"
                  :review-alongside="taskForm.editor.value"
                  @review="onReview"
                />
              </template>
              <template #skill="{ id }">
                <SkillProposalCard
                  v-if="skills.proposals.value.some((s) => s.id === id) || skills.saved.value.some((s) => s.id === id)"
                  :proposals="skills.proposals.value.filter((s) => s.id === id)"
                  :saved="skills.saved.value.filter((s) => s.id === id)"
                  :busy="skills.busy.value"
                  :error="skills.error.value"
                  :member-names="memberNames"
                  @save="skills.save"
                  @decline="skills.decline"
                  @open="openSkillPage"
                />
                <BaseEmptyState
                  v-else
                  size="compact"
                  icon=""
                  :title="t('work.room.workCard.settled')"
                  :action="t('work.room.workCard.viewSkill')"
                  @action="openSkillPage({ id })"
                />
              </template>
              <template #overview>
                <TaskOverview
                  v-if="taskId && currentTask"
                  ref="taskOverviewRef"
                  :room="selectedTopic"
                  :task="currentTask"
                  :member-names="memberNames"
                  :activity-tick="activityTick"
                  :topic-list="store.topics"
                  :agent-name="store.agentName"
                  :agent-handle="store.agentHandle"
                  :members="store.members"
                  :checklist="taskOverview.checklist.value"
                  :related="taskOverview.related.value"
                  :can-retry="taskPage.takesPart.value"
                  :retrying="taskOverview.retrying.value"
                  :retry-error="taskOverview.retryError.value"
                  @open-topic="openTopic"
                  @mention-click="handleMentionClick"
                  @retry-opening="taskOverview.retryOpening"
                  @open-output="(path: string) => panelRef?.openFile?.(path)"
                  @open-file="(path: string) => panelRef?.openFile?.(path)"
                  @open-document="(id: string, title: string) => panelRef?.openDocument?.({ id, title })"
                  @open-source="openSource"
                />
                <ChannelOverview
                  v-else-if="!taskId"
                  :topic="selectedTopic"
                  :general="selectedTopic.kind === 'root'"
                  :overview="channelOverview.overview.value"
                  :overview-text="channelOverview.overviewText.value"
                  :pins="channelOverview.pins.value"
                  :tasks="channelOverview.tasks.value"
                  :can-pin="canPin"
                  :member-names="memberNames"
                  :agent-name="store.agentName"
                  :save-description="(text: string) => store.describe(topicId, text)"
                  :threads="channelThreads.rows.value"
                  :can-create-thread="canOpenThread"
                  :files="roomFiles.rows.value"
                  :routines="channelOverview.routines.value"
                  :can-create-task="selectedTopic.status !== 'archived'"
                  @unpin="channelOverview.unpin"
                  @edit-overview="
                    (id: string) => panelRef?.openDocument?.({ id, title: t('work.channel.overview.project') })
                  "
                  @jump="jumpTo"
                  @open-task="(id: string) => (mdAndUp ? openFromOverview(id) : openTaskPage(id))"
                  @open-delivery="(id: string) => (mdAndUp ? openFromOverview(id) : openTask(id))"
                  @open-all="router.push({ name: 'project-tasks', params: { projectId }, query: { channel: topicId } })"
                  @mention-click="handleMentionClick"
                  @open-thread="showThread"
                  @new-thread="newThreadOpen = true"
                  @open-file="openOverviewFile"
                  @open-routine="openRoutine"
                  @new-routine="panelRef?.newRoutine?.()"
                  @new-task="newTask(selectedTopic, router)"
                  @show="showList"
                />
              </template>
              <!-- Desktop: the delivery under review, in a block of its own; beside the conversation its
                   head goes into that block's title bar (head). -->
              <template v-if="mdAndUp" #delivery="{ id, head }">
                <WorkPanel
                  :key="id"
                  class="tab-fill"
                  v-bind="reviewPanel(id)"
                  :head-target="head"
                  :review-head="{
                    heading: taskFor(id) ? taskTitle(taskFor(id)!) : '',
                    version: taskFor(id)?.card?.version ?? null,
                    state: stateOf(taskFor(id)),
                  }"
                  @open-topic="openTopic"
                  @open-card="openTaskPage"
                  @mention-click="handleMentionClick"
                  @navigate="router.push($event)"
                  @update:tab="(key: string) => (reviewView = { taskId: id, view: key })"
                  @locate="(payload: PreviewLocate) => reviewChat.say(id, payload)"
                  @return-asked="returnAsk = null"
                >
                  <!-- The task is a thread: its own conversation, the request it came from on top. The
                     decision is in the head above, so the conversation does not carry a second one. -->
                  <template #chat>
                    <TopicChatColumn
                      :ref="(handle: unknown) => reviewChat.hold(id, handle)"
                      class="col col-chat flex-grow-1"
                      beside
                      accept-elsewhere
                      :topic="selectedTopic"
                      :task-id="id"
                      :task-agent-handle="taskFor(id)?.agent_handle ?? null"
                      :members="store.members"
                      :topic-list="store.topics"
                      :member-names="memberNames"
                      :composer-closed="composerClosed"
                      :ask-closed="askClosed || taskFor(id)?.status === 'closed'"
                      @open-file="(path: string) => panelRef?.openFile?.(path)"
                      @open-topic="openTopic"
                      @open-card="openTask"
                      @mention-click="handleMentionClick"
                    />
                  </template>
                </WorkPanel>
              </template>
              <template v-if="!taskId" #threads="{ head }">
                <!-- Desktop: an open thread stays in the 支线 block, its head (with a back to the list) in the block's title bar. -->
                <ThreadPane
                  v-if="threadId && mdAndUp"
                  class="tab-fill"
                  :head-target="head"
                  :room="selectedTopic"
                  :thread-id="threadId"
                  :focus-block="threadFocusBlock"
                  :members="store.members"
                  :topic-list="store.topics"
                  :member-names="memberNames"
                  :to-task="upgradeMessage"
                  @close="backToThreads"
                  @open-task="openTaskPage"
                  @as-task="showAsTask"
                  @open-file="(path: string) => panelRef?.openFile?.(path)"
                  @open-topic="openTopic"
                  @mention-click="handleMentionClick"
                />
                <PanelThreads
                  v-else
                  :rows="channelThreads.rows.value"
                  :recycled="channelThreads.recycled.value"
                  :can-restore="canRestoreThread"
                  :loading="channelThreads.loading.value"
                  :error="channelThreads.error.value"
                  :refs="{ mentionNames: memberNames, topicTitles: {} }"
                  :name-of="(handle: string) => memberNames[handle] || handle"
                  :fmt-time="threadTime"
                  :can-create="canOpenThread"
                  @open="showThread"
                  @open-task="openTaskPage"
                  @new="newThreadOpen = true"
                  @restore="restoreAThread"
                />
              </template>
              <!-- 手机：一屏放不下两栏，对话是 tab 栏里的第一格。 -->
              <template #chat>
                <TopicChatColumn
                  ref="chatColumn"
                  class="col col-chat flex-grow-1"
                  :topic="selectedTopic"
                  :members="store.members"
                  :topic-list="store.topics"
                  :unread-on-open="unreadOnOpen"
                  :focus-block="focusBlock"
                  :task-id="taskId ?? null"
                  :task-agent-handle="currentTask?.agent_handle ?? null"
                  :member-names="memberNames"
                  :composer-closed="composerClosed"
                  :ask-closed="askClosed"
                  :chapters="chapters.rows.value"
                  :chapterable="canChapter"
                  :chapter-ids="chapters.chapterIds.value"
                  accept-review-button
                  :adopting-task="adoptingTask"
                  v-on="chatEvents"
                  @chapter="chapters.add"
                  @unchapter="chapters.remove"
                  @rename="chapters.rename"
                  @open-room="backToRoom"
                />
              </template>
            </WorkPanel>
            <!-- 手机上对话和「改动」是两个页签，在「改动」页签里决定，采纳那一条在这一格底部。 -->
            <TopicAcceptCard
              v-if="selectedTopic && !mdAndUp && panelTab === 'changes'"
              class="panel-accept"
              :topic-id="selectedTopic.id"
              :task-id="taskId ?? undefined"
              :topic-status="selectedTopic.status"
              @review="onReview"
            />
          </div>
        </Teleport>
      </div>
    </template>
    <NewThreadDialog
      v-if="newThreadOpen && selectedTopic"
      v-model="newThreadOpen"
      :busy="creatingThread"
      :channel="topicTitle(selectedTopic)"
      @create="createThread"
    />
  </div>
</template>

<style scoped>
/* 这条不参与伸缩：它有内容时占自己那点高度，没内容时整个不在 DOM 里，四格的高度
   都不会因为它变来变去。 */
.topic-view {
  flex: 1 1 auto;
  overflow: hidden;
  /* 底色分层：侧栏坐在 background (--canvas) 上，内容区是它上面那张 surface。
     这条视图以前不画底、直接透出 body 的 --canvas，于是侧栏和正文同色，两者
     之间只剩一条边线在撑。 */
  background: var(--surface);
  /* Switching topics on a wide screen cross-fades this view only (lib/viewTransition.ts). */
  view-transition-name: topic-view;
}
.tab-fill {
  flex: 1 1 auto;
  min-width: 0;
}
.task-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
</style>

<style scoped src="./topic-panes.css"></style>
