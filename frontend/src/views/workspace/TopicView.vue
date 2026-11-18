<script setup lang="ts">
import type { WorkTabMeta } from '@/components/work/workLayout'
import type { RoomTask } from '@/cx_types'
import type { TaskSource } from '@/types/taskOrigin'
import type { TaskOverviewHandle } from './topicView/actions'
import type { ChatColumnHandle, WorkPanelHandle } from './topicView/shared'

import { computed, defineAsyncComponent, onMounted, onUnmounted, provide, ref, toRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { useQuery } from '@tanstack/vue-query'

import { provideAcceptCard } from '@/composables/useAcceptCard'
import { useChapters } from '@/composables/useChapters'
import { usePageTitle } from '@/composables/usePageTitle'
import { useScreenHeader } from '@/composables/useScreenHeader'

import { useTopicActions } from './topicView/actions'
import { useTopicAddress } from './topicView/address'
import { useAdoptInPlace } from './topicView/adoptInPlace'
import { useCardPhase, useTopicConversation } from './topicView/conversation'
import { useDeliveryTab } from './topicView/delivery'
import { useTopicHeader } from './topicView/header'
import { useTopicLive } from './topicView/live'
import { useTopicPanelState } from './topicView/panel'
import { useSkillProposals } from './useSkillProposals'

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
import WorkPanel from '@/components/WorkPanel.vue'
import { t } from '@/i18n'
import { usePagePanels } from '@/layouts/pagePanels'
import { PENDING_NOTES, usePendingNotes } from '@/lib/pendingNotes'
import { mayCommentIn } from '@/lib/projectPermissions'
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
import { CONVERSATION, useRoomLayout } from '@/views/workspace/roomLayout'
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
const taskOverviewRef = ref<TaskOverviewHandle | null>(null)

// ---- 右侧面板的收 / 开（三档见 useTopicPanel）----
const panelState = useTopicPanelState({
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
})
// ---- 在频道里审阅的那件交付、右栏开着哪一格（见 topicView/delivery.ts）----
const reviewReload = ref(0)
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
const deliveryTabsBinding = computed(() =>
  mdAndUp.value
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
/** 工作面板此刻有哪几格：固定的几格（面板算好的名字和信号，没挂上时照表），打开的任务，打开的文件。 */
function workTabs() {
  const handle = panelRef.value as (WorkPanelHandle & { fixedMeta?: WorkTabMeta[]; fileMeta?: WorkTabMeta[] }) | null
  const fixed =
    handle?.fixedMeta ??
    roomTabs({ withChat: false, review: false, task: !!props.taskId }).map((tab) => ({
      key: tab.key,
      title: tab.label,
      icon: tab.icon,
    }))
  return [
    ...fixed.map((tab) => ({ ...tab, alwaysRender: tab.key === 'preview' })),
    ...deliveryTabsBinding.value.map((tab) => ({
      key: `delivery:${tab.id}`,
      title: tab.title || t('work.room.delivery.title'),
      icon: 'mdi-file-eye-outline',
      signal: tab.dot ? ({ kind: 'dot' } as const) : undefined,
      transient: true,
      alwaysRender: true,
    })),
    ...(handle?.fileMeta ?? []).map((tab) => ({ ...tab, transient: true, alwaysRender: true })),
  ]
}
const room = useRoomLayout(usePagePanels, {
  kind: () => (mdAndUp.value ? (props.taskId ? 'task' : 'room') : null),
  tabs: workTabs,
  workHidden: () => !roomReady.value || !docked.value,
  floating: () => panelFloat.value,
  hide: { label: () => closeLabel.value, run: () => closePanel() },
  onActivated: (key) => (panelRef.value as { activated?: (key: string) => void } | null)?.activated?.(key),
  onClose: (key) => (panelRef.value as { closeTab?: (key: string) => void } | null)?.closeTab?.(key),
  onKeep: (key) => (panelRef.value as { keepTab?: (key: string) => void } | null)?.keepTab?.(key),
})
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

// 预览里的文件评论能不能写（判据见 `mayCommentIn`）。
const canComment = computed(() => {
  const topic = selectedTopic.value
  return mayCommentIn(
    topic,
    store.projects.find((p) => p.id === topic?.project_id)
  )
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
        @return-asked="returnAsk = null"
      >
        <template #chat>
          <TopicChatColumn
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
              :can-comment="canComment"
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
              @open-topic="openTopic"
              @open-card="openTaskPage"
              @mention-click="handleMentionClick"
              @update:tab="onWorkPanelTab"
              @close-delivery="closeDelivery"
              @viewing="viewingPath = $event"
              @locate="onLocate"
              @navigate="router.push($event)"
            >
              <!-- 对话看不见时，采纳那一条贴在读者在看的那一格底下：要做的决定不能跟着消失。 -->
              <template v-if="mdAndUp && acceptElsewhere && selectedTopic" #dock>
                <TopicAcceptCard
                  class="panel-accept"
                  :topic-id="selectedTopic.id"
                  :task-id="taskId ?? undefined"
                  :topic-status="selectedTopic.status"
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
                  @unpin="channelOverview.unpin"
                  @edit-overview="
                    (id: string) => panelRef?.openDocument?.({ id, title: t('work.channel.overview.project') })
                  "
                  @jump="jumpTo"
                  @open-task="openTaskPage"
                  @open-delivery="openTask"
                  @open-all="router.push({ name: 'project-tasks', params: { projectId }, query: { channel: topicId } })"
                  @mention-click="handleMentionClick"
                />
              </template>
              <!-- Desktop: the delivery under review, in its own closable tab. -->
              <template v-if="mdAndUp" #delivery="{ id }">
                <WorkPanel
                  :key="id"
                  class="tab-fill"
                  v-bind="reviewPanel(id)"
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
                  @return-asked="returnAsk = null"
                >
                  <!-- The task is a thread: its own conversation, the request it came from on top. The
                     decision is in the head above, so the conversation does not carry a second one. -->
                  <template #chat>
                    <TopicChatColumn
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
              <template v-if="!taskId" #threads>
                <!-- Desktop: an open thread stays in the 支线 tab, with a back to the list. -->
                <ThreadPane
                  v-if="threadId && mdAndUp"
                  class="tab-fill"
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
