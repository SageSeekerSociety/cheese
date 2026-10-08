<script setup lang="ts">
import type { AgentControlState, Block, ChatAttachment, Topic, TopicMemberRow } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { CardPhase } from '@/lib/topicState'
import type { PreviewLocate, SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useChannelThreads } from '@/composables/useChannelThreads'
import { useEscapeLayer } from '@/composables/useEscapeStack'
import { usePageTitle } from '@/composables/usePageTitle'
import { useRoomTabHistory } from '@/composables/useRoomTabHistory'
import { useTopicPanel } from '@/composables/useTopicPanel'

import { getTask } from '@/api/tasks'
import { openThread } from '@/api/threads'
import BaseButton from '@/components/base/BaseButton.vue'
import ChannelOverview from '@/components/channel/ChannelOverview.vue'
import { useTopBarBack } from '@/components/common/topBarBack'
import PanelThreads from '@/components/panels/PanelThreads.vue'
import PushPermissionPrompt from '@/components/PushPermissionPrompt.vue'
import TaskHeader from '@/components/task/TaskHeader.vue'
import TaskOverview from '@/components/task/TaskOverview.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import TopicHeader from '@/components/TopicHeader.vue'
import WorkPanel from '@/components/WorkPanel.vue'
import { t } from '@/i18n'
import { agentNames, memberName } from '@/lib/agentNames'
import { warmRoutesWhenIdle } from '@/lib/routePrefetch'
import { cachedTopicPanel, fetchTopicMembers } from '@/lib/topicPanelCache'
import { onTopicRosterChange } from '@/lib/topicRosterChanges'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { userRefRoute } from '@/lib/userRef'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import TopicChatColumn from '@/views/workspace/TopicChatColumn.vue'
import { useChannelOverview } from '@/views/workspace/useChannelOverview'
import { useTaskOverview } from '@/views/workspace/useTaskOverview'
import { useTaskPage } from '@/views/workspace/useTaskPage'
import { useTopicPanes } from '@/views/workspace/useTopicPanes'

// 话题视图: ONE topic header, then the chat | 工作面板 split. A task in the room
// is drawn by the same view with the task's header, its own conversation in the
// chat column and its own 概览 / 现场 / 改动 / 预览 in the panel. The input bar is
// the chat column's own — it used to span both columns from here, which read as
// addressing the whole topic while 99% of what it sent was a chat message only
// the left column shows. Which topic is open is a route param, and ProjectShell
// keys this view on it: every topic gets a fresh instance, so nothing below — or in
// any child — can carry one topic's state into the next.
defineOptions({ name: 'TopicView' })

// 支线那一半只在打开一条支线时才要，用到时再取。
const ThreadPane = defineAsyncComponent(() => import('@/views/workspace/ThreadPane.vue'))

// `taskId`：地址指着这个房间里的一个任务时，画的是任务页。`threadId`：指着频道里的一条
// 支线时，桌面上支线占右边那一半，手机上是一整页。
const props = defineProps<{ projectId: string; topicId: string; taskId?: string; threadId?: string }>()
const { mdAndUp } = useDisplay()
const router = useRouter()
const route = useRoute()
const store = useWorkspaceStore()

// 「你在看什么」进 URL (提案 A): which tab of the work panel is open, so
// 「你来看一眼这个 diff」 is a link someone can send. The panel reports its own
// moves; the address is this page's business, not the panel's.
const panelTab = computed(() => {
  const q = route.query.tab
  return typeof q === 'string' ? q : undefined
})
// 页签和浏览器历史怎么对应（桌面 replace；手机上 Back 先回到对话）见 useRoomTabHistory。
const phone = computed(() => !mdAndUp.value)
const tabHistory = useRoomTabHistory(phone)
function onPanelTab(key: string) {
  tabHistory.goTab(key)
}
// 手机上不在对话那一格时，顶栏的 ← 和 Back 一样先回到对话。
useTopBarBack(() =>
  phone.value && !tabHistory.onChat.value ? { label: t('work.room.backToChat'), onBack: tabHistory.toChat } : null
)
void tabHistory.ensureChatBehind()

// 「去验收」：决策在聊天，审查在面板 —— 它不把人带去任何地方，只把右栏切到
// 「改动」那一格。对话栏末尾那张验收卡和概览里那张卡上的按钮是同一个动作，所以
// 只有这一处定义（`chatEvents.review` 和 `<WorkPanel @review>` 都指过来）。
function onReview() {
  onPanelTab('changes')
}

// 地址点名的一条消息（搜索结果、别人发来的链接）：对话栏打开时停在它上面。
const focusBlock = computed(() => {
  const q = route.query.block
  return typeof q === 'string' && q ? q : null
})
// 任务有自己的页面：点开一个任务就去那里，浏览器的返回退回房间。
function onOpenCard(taskId: string) {
  void router.push({ name: 'workspace-task', params: { projectId: props.projectId, topicId: props.topicId, taskId } })
}
// 已经写进提交历史的旧链接（`?card=<任务>`）：那一层下钻已经没有了，任务有自己
// 的页面，照着链接来的人直接送过去。
watch(
  () => route.query.card,
  (card) => {
    if (typeof card !== 'string' || !card) return
    void router.replace({
      name: 'workspace-task',
      params: { projectId: props.projectId, topicId: props.topicId, taskId: card },
    })
  },
  { immediate: true }
)
function backToRoom() {
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId: props.topicId } })
}

// Bumped on every AI turn / tool use. Watched by the documents in the panel
// and the project overview on 综合's overview (reload what 芝士 maintained).
const activityTick = ref(0)

// 频道概览：置顶、任务、综合的项目总览。
const channelOverview = useChannelOverview({
  channelId: () => (props.taskId ? null : props.topicId),
  projectId: () => props.projectId,
  general: () => store.placeById(props.topicId)?.kind === 'root',
  tick: () => activityTick.value,
  reportError: (e) => store.reportError(e, t('work.channel.pins.unpinFailed')),
})
const canPin = computed(() => {
  const topic = selectedTopic.value
  return !!topic && topic.status !== 'archived' && (topic.kind === 'root' || !!topic.joined)
})
function jumpTo(blockId: string) {
  void router.replace({ query: { ...route.query, block: blockId } })
}

// ---- 支线 ----
// 主线上一条消息的支线：有就打开，没有就先开一条。概览里「支线」那一格读同一份清单。
const channelThreads = useChannelThreads(() => (props.taskId ? null : props.topicId))
void channelThreads.load()
function showThread(threadId: string) {
  channelThreads.markSeen(threadId)
  void router.push({
    name: 'workspace-thread',
    params: { projectId: props.projectId, topicId: props.topicId, threadId },
  })
}
async function onOpenThread(block: Block) {
  if (block.thread?.id) return showThread(block.thread.id)
  try {
    showThread((await openThread(block.id)).id)
  } catch (e) {
    store.reportError(e, t('work.room.thread.openFailed'))
  }
}

// ---- 右侧面板的收 / 开（三档见 useTopicPanel）----
const panesRef = ref<HTMLElement | null>(null)
const panel = useTopicPanel({
  panes: panesRef,
  tab: panelTab,
  desktop: mdAndUp,
  thread: computed(() => !!props.threadId),
  setTab: (tab) => void router.replace({ query: { ...route.query, tab } }),
  showing: () => panelRef.value?.activeTab(),
})
const panelOpen = panel.open
const panelFloat = panel.float
function closePanel() {
  if (props.threadId) return backToRoom()
  panel.hide()
  // Esc 关掉浮层，焦点回到打开它那颗开关（键盘和读屏用户必须回得去）。
  void nextTick(() => document.querySelector<HTMLElement>('[data-panel-toggle]')?.focus())
}
function togglePanel() {
  if (panelOpen.value) closePanel()
  else panel.show()
}
// 浮层按 Esc 收起，焦点回到页头那颗开关。窄档里二级侧栏浮层也可能同时开着，两层
// 共用一个 Esc 栈：一下 Esc 只关最上面那层（后打开的那层）。见 useEscapeStack。
useEscapeLayer(
  computed(() => panelFloat.value && panelOpen.value),
  closePanel
)

const AUTHOR = myHandle()

// URL 里的这个 id 指向一个房间。列表里没有就直接去问它——深链接、刷新，都走这条路。
const selectedTopic = computed<Topic | null>(() => store.placeById(props.topicId))

// ---- 任务页 ----
// 页头、概览、能不能说话都读这一份；对话和面板的其余几格按任务的 id 自己读。
// 任务交给谁、请谁协作，从项目里的人挑：被选中的人随之加入这个频道（后端）。
const projectPeople = computed<TopicMemberRow[]>(() =>
  store.members
    .filter((m) => !m.agent && m.active !== false)
    .map((m) => ({
      topic_id: props.topicId,
      member_handle: m.user_handle,
      role: 'member' as const,
      name: m.name,
      avatar_id: m.avatar_id ?? null,
    }))
)
const taskPage = useTaskPage({ taskId: () => props.taskId, people: () => projectPeople.value })
// 第一次由 openPlace 记已读；之后在同一个频道里进出任务，页面不重建，换到哪段对话
// 就是读了哪段。
let placeOpened = false
watch(
  () => props.taskId,
  (taskId) => {
    taskPage.reset()
    void taskPage.load()
    if (placeOpened) store.markRead(taskId ?? props.topicId)
  },
  { immediate: true }
)
/** 正在看的这一段对话：任务页是任务的，否则是频道自己的。已读游标记在它上面。 */
const conversationId = computed(() => props.taskId ?? props.topicId)
const {
  task: currentTask,
  loading: taskLoading,
  loadError: taskLoadError,
  isOwner: taskOwner,
  isOpen: taskOpen,
  people: taskPeople,
  machine: taskMachine,
  machineError: taskMachineError,
  starting: taskStarting,
  startError: taskStartError,
  actionError: taskActionError,
  comparing: taskComparing,
  comparison: taskComparison,
  compareError: taskCompareError,
} = taskPage
// 任务里只有负责人能说话，关了就谁都不能说了：输入框的位置换成这一句。
const composerClosed = computed(() => {
  const task = taskPage.task.value
  if (!props.taskId) {
    // 频道的主线：归档了只能看；没加入的人在这里只读，加入后才能说话（支线里照样能回）。
    const topic = selectedTopic.value
    if (topic?.status === 'archived') return t('work.channel.archivedNotice')
    return topic?.joined === false ? t('work.channel.notJoined') : null
  }
  if (!task) return null
  if (!taskPage.isOpen.value) return t('work.task.closedNotice')
  return taskPage.takesPart.value ? null : t('work.task.ownerOnlyNotice', { name: store.agentName })
})

// 手机顶栏写的是当前页的标题，而这一页的标题是话题名——路由上没有，只有打开了
// 才知道。桌面顶栏不显示它，但浏览器标签页同样受益。
const { setDynamicTitle, clearDynamicTitle } = usePageTitle()
watch(
  () => [selectedTopic.value, taskPage.task.value] as const,
  ([topic, task]) => {
    if (props.threadId) setDynamicTitle(t('work.room.thread.title'), 'workspace-thread')
    if (props.taskId && task) setDynamicTitle(taskTitle(task), 'workspace-topic')
    else if (topic) setDynamicTitle(topicTitle(topic), 'workspace-topic')
    else clearDynamicTitle('workspace-topic')
  },
  { immediate: true }
)
onUnmounted(() => clearDynamicTitle('workspace-topic'))
onUnmounted(() => clearDynamicTitle('workspace-thread'))
/** 支线清单里最后一条回复的时间：今天的写钟点，更早的写日期。 */
function threadTime(iso: string): string {
  const at = new Date(iso)
  return at.toDateString() === new Date().toDateString()
    ? at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : at.toLocaleDateString([], { month: 'numeric', day: 'numeric' })
}

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
// The list is still on its way, so "not found" is not yet a fact. Neither is it
// one while this id is being asked about directly — the path a deep link takes.
const resolving = computed(
  () =>
    !selectedTopic.value && (store.loadingTopics || store.topics.length === 0 || store.isResolvingPlace(props.topicId))
)

function openTopic(topicId: string) {
  if (topicId === props.topicId) return
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

// ---- 左右两栏：分隔、专注模式、对话收起时的窄边（见 useTopicPanes）----
const panes = useTopicPanes({ panelOpen, panelFloat, desktop: mdAndUp })
const { focusMode, focusIcon, focusLabel, docked, chatNews, noteChatNews, toggleFocus, chatStyle } = panes
const { freezeChatWidth, startPaneDrag } = panes
const closeLabel = computed(() => t('work.room.panel.close'))
const panelRef = ref<{
  showOverview: () => Promise<void>
  openFile?: (path: string, taskId?: string | null) => void
  siteBlock?: (block: Block) => void
  openDocument?: (document: OpenedDocument, review?: DocReviewRequest) => Promise<void>
  previewShown?: () => void
  // 面板此刻在画哪一格。收起再打开要回到它——自动选中的那一格不在地址里，只能问它。
  activeTab: () => string
} | null>(null)
const panelAcceptRef = ref<{ reload: () => Promise<void> } | null>(null)
function reloadAccept() {
  chatColumn.value?.reloadAccept()
  void panelAcceptRef.value?.reload()
}
const chatColumn = ref<{
  connected: boolean
  reloadAccept: () => void
  reloadFeedback: () => void
  reloadSkills: () => void
  say: (content: string, attachments?: ChatAttachment[]) => boolean
  submitQuestion: SubmitPreviewQuestion
} | null>(null)

// The chat column's own composer is the one this topic uses; TopicView only
// needs a handle on the panel it lives in for the connection dot in the header.
const composerReady = computed(() => !!chatColumn.value?.connected)

// 预览里指出的一处位置，作为一条普通消息进这个房间的对话。没有新接口，也没有
// 长期锚点：它只在下一轮被读一次。
function onLocate(payload: PreviewLocate) {
  chatColumn.value?.say(payload.message, payload.attachments)
}
const submitQuestion: SubmitPreviewQuestion = (request) => chatColumn.value?.submitQuestion(request) ?? false

// 对话那一栏在两端挂在不同位置（左栏 / tab 栏第一格），但接的是同一组事件。
const chatEvents = {
  'turn-done': handleTurnDone,
  working: handleWorking,
  activity: (lines: MemberActivityLine[]) => (activity.value = lines),
  'agent-control': (state: AgentControlState) => (agentControl.value = state),
  'site-block': (block: Block) => panelRef.value?.siteBlock?.(block),
  'site-turns': (turns: Record<string, number>) => (siteTurns.value = turns),
  'state-changed': handleStateChanged,
  // 芝士摆出来一份东西：面板立刻看一眼当前预览，不等轮询。
  'preview-shown': () => panelRef.value?.previewShown?.(),

  'mention-click': handleMentionClick,
  'open-file': (path: string) => panelRef.value?.openFile?.(path),
  'open-resource': handleOpenResource,
  'upgrade-message': handleUpgradeMessage,
  'open-thread': onOpenThread,
  'open-topic': openTopic,
  'open-card': onOpenCard,
  phase: (p: CardPhase) => (cardPhase.value = p),
  review: onReview,
}

// 有没有队友正在这个话题里跑一轮 —— 工作面板的「现场」那一格和推送提示读它。
const working = ref(false)

// 任务概览那一列要的：相关、这一轮的清单、第一轮失败后的重试。
const taskOverview = useTaskOverview({
  taskId: () => props.taskId,
  working: () => working.value,
  reloadTask: () => taskPage.load(true),
})
const taskOverviewRef = ref<InstanceType<typeof TaskOverview> | null>(null)
function openDiscussion(conversationId: string) {
  if (conversationId === props.topicId) backToRoom()
  else showThread(conversationId)
}

// 页头那颗点说的是「这个房间跟不跟得上」——它和工作条必须同源。对话栏报上来的
// `composerReady` 是 socket 的那一帧，而 socket 会在连接打嗝时闪断：那一瞬它说
// 未连接，可这一轮还在跑（工作条写着「正在工作 · 重试中」，因为重试就是靠它自己
// 接着干）。一轮没跑完，这个房间就是连着的 —— 断了它没法把这一轮干完。所以两个
// 一起看：只要工作条在说「正在工作」，页头就不能同时说「未连接」。
const roomConnected = computed(() => composerReady.value || working.value)
// 此刻谁在这个房间里忙，对话栏从 socket 上学来：现场那一格画其中在干活的队友。
const activity = ref<MemberActivityLine[]>([])
// 会话状态的最近一帧，对话栏从 socket 上收到，现场那格的会话详情读它。
const agentControl = ref<AgentControlState | null>(null)
// 正在跑的轮次各自从什么时候开始，对话栏从 socket 上算出来，现场读它分出哪一组还在进行。
const siteTurns = ref<Record<string, number>>({})

// ---- 采纳卡处在哪一段 ----
// The accept card owns its own data, but the panel opens on the tab its stage
// calls for, so the one word travels up rather than the card list travelling
// out. undefined until the card box has actually answered — the panel does not
// get to pick a tab on an answer nobody has yet. The room header does not show
// it: a card's stage is the card's, shown on the card and on the board.
const cardPhase = ref<CardPhase | undefined>(undefined)

// 芝士 开工 / 收工，由对话栏按轮次生命周期报上来。这是 `working` 唯一的开关：
// 「现场」那一格的存在与否读它，所以它必须在开工那一刻就翻过来——而不是等到它第
// 一次动手。一条 @芝士 开出来的 agent 可能先想上半分钟才动手，那半分钟里右边什
// 么都没有，除非刷新一次页面。
function handleWorking(now: boolean) {
  working.value = now
}

function handleTurnDone() {
  // 这一轮干完了 —— 现场那条时间线就是它留下的记录。
  working.value = false
  noteChatNews()
  activityTick.value += 1
  if (props.taskId) void taskPage.load(true)
  void store.refreshTopics()
  // 芝士's reply landed after our read cursor — the user is watching this
  // topic, so re-bump the cursor before refreshing badges (other topics that
  // got messages in the background DO light up).
  store.markRead(conversationId.value)
  void store.refreshUnread()
}

// A platform resource in this room changed (the API handler that changed it
// sent the frame) — refresh the affected panel live (§3.1.1).
function handleStateChanged(resource: string, id?: string) {
  // 「topics」也说任务清单变了（建、改名、关），任务页自己的页头和侧栏都要跟着变。
  if (resource === 'topics') {
    // 后端指名了变的是哪一行（房间 id）就只重取那一行 —— 改一个房间名不再重下整份
    // 清单（400 多个话题近 300KB）。没指名（老后端、或没带 id 的调用点）退回整块重取。
    if (id) void store.refreshTopicRow(id)
    else void store.refreshTopics()
    store.noteTasksChanged()
    if (props.taskId) void taskPage.load(true)
    else void channelOverview.loadTasks()
  } else if (resource === 'pins') void channelOverview.loadPins()
  else if (resource === 'accept') reloadAccept()
  // 提案卡落下、被发出去、被「不用」：卡片跟着变，不等刷新。
  else if (resource === 'feedback') chatColumn.value?.reloadFeedback()
  // 技能的提议落下、被保存或被拒：那张卡跟着变。
  else if (resource === 'skills') chatColumn.value?.reloadSkills()
  else if (resource === 'tasks' && props.taskId) void taskPage.load(true)
  // 频道里有支线长了一条：概览里「支线」那一格跟着变（主线上那一行对话栏自己换）。
  else if (resource === 'threads') void channelThreads.load()
  else activityTick.value += 1 // doc / notify → reload
}

// An action card's button → open the relevant view (§3.1.1 控件).
async function handleOpenResource(
  resource: string,
  turnId?: string,
  review?: DocReviewRequest,
  document?: OpenedDocument
) {
  if (resource === 'doc' && document) {
    // 资料库里的一份文档：在面板的自由区开一格，不是这个对话自己的文档。
    focusMode.value = false
    await nextTick()
    await panelRef.value?.openDocument?.(document, review)
    return
  }
  if (resource === 'site') {
    // 对话里在动的那个头像：它此刻在干什么，去现场看。
    focusMode.value = false
    onPanelTab('site')
  } else if (resource === 'changes') {
    // 本轮摘要的「查看改动」: the diff is a tab away, not a new page.
    focusMode.value = false
    onPanelTab('changes')
  } else if (resource === 'accept') {
    reloadAccept()
  } else if (resource === 'doc') {
    // B1 Phase 2: highlight the exact paragraphs this turn changed (falls back to
    // a whole-doc pulse when the turn's blocks aren't tagged). Leaving focus mode
    // re-renders the editor, which recreates its DOM — wait for that render to
    // settle before highlightTurn tags + flashes, or the flash is wiped instantly.
    // 只有任务有自己的文档；频道里说的是资料库里的那份，上面那条已经接住了。
    if (!props.taskId) return
    focusMode.value = false
    panel.show()
    await panelRef.value?.showOverview()
    // 「查看改动」：在正文里一处处标出这个人让 AI 队友改的那几处。
    if (review) taskOverviewRef.value?.reviewEdits(review)
    else if (turnId) taskOverviewRef.value?.highlightTurn(turnId)
    else taskOverviewRef.value?.pulse()
  }
  // topics: the topic panel is already in view next to the chat.
}

// A clicked <@handle> mention chip → open that teammate's member page.
function handleMentionClick(handle: string) {
  void router.push(userRefRoute(handle, props.projectId))
}

// 转为任务 from a message bubble: the message becomes a task in this channel.
async function handleUpgradeMessage(messageId: string) {
  const taskId = await store.upgradeMessage(messageId)
  if (taskId) onOpenCard(taskId)
}

// 「新消息从哪开始」只有开话题的那一瞬间知道：markRead 一跑，未读数就归零了。
// 所以在归零之前抓一次，交给对话栏去画那条线。
const unreadOnOpen = store.unreadMap[props.topicId]?.messages ?? 0

// 这个房间名册上每个 handle 叫什么。「现场」那一格给每一行署名用它，人和 AI 队
// 友一个规矩：署作者，不署「这个房间的那位」——一个房间可以先后交给两个队友。
// 那一格自己不拉名册，所以在这里拉一次传下去。AI 队友的名字和对话栏同一个出处
// （`agentNames`）：已经不在这间房里的队友，项目名册上还叫得出。
const roomMembers = ref<TopicMemberRow[]>(cachedTopicPanel('members', props.topicId)?.data ?? [])
const memberNames = computed<Record<string, string>>(() => ({
  ...Object.fromEntries(roomMembers.value.map((m) => [m.member_handle, memberName(m) || m.member_handle])),
  ...Object.fromEntries(agentNames(roomMembers.value, store.members)),
}))
async function loadMemberNames() {
  try {
    roomMembers.value = (await fetchTopicMembers(props.topicId)).data
  } catch {
    // 名册拉不到，现场那一格就按 handle 署名——比空白好，也比报错好。
  }
}
void loadMemberNames()

// 名册抽屉里加了人、移了人，这份跟着重拉：刚请进来的队友在「现场」那一格也要叫得出名字。
onUnmounted(
  onTopicRosterChange((topicId) => {
    if (topicId === props.topicId) void loadMemberNames()
  })
)

// 这个 id 在侧栏那张表里找不到的话，直接问它——支线走的永远是这条路。
// 先等它答完再记已读：已读位只有房间有，不知道这是房间还是支线就记，
// 等于对每一条支线都白打一次会 404 的请求。
// A room that became a task keeps its id, so an old link, a bookmark or the
// last room remembered for the project still names it: such an id opens the
// task's page in its channel instead of saying the room is gone.
const redirecting = ref(false)
async function openPlace() {
  await store.loadPlace(props.topicId)
  if (store.placeById(props.topicId)) {
    placeOpened = true
    store.markRead(conversationId.value)
    return
  }
  if (props.taskId) return
  redirecting.value = true
  try {
    const task = await getTask(props.topicId)
    if (task.project_id !== props.projectId) return
    await router.replace({
      name: 'workspace-task',
      params: { projectId: props.projectId, topicId: task.room_id, taskId: task.id },
      query: route.query,
    })
  } catch {
    // Not a task either: the empty state below says so.
  } finally {
    redirecting.value = false
  }
}
void openPlace()
</script>

<template>
  <div class="topic-view d-flex flex-column fill-height" style="min-width: 0">
    <div v-if="!selectedTopic" class="flex-grow-1 d-flex align-center justify-center">
      <v-progress-circular v-if="resolving || redirecting" indeterminate color="primary" />
      <div v-else class="text-center">
        <div class="t-body c-muted">{{ t('work.topic.notFound') }}</div>
        <div class="t-meta mt-1">{{ t('work.topic.notFoundHint') }}</div>
      </div>
    </div>

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
      <TaskHeader
        v-if="taskId"
        :room="selectedTopic"
        :task="currentTask"
        :member-names="memberNames"
        :agent-name="store.agentName"
        :people="taskPeople"
        :machine="taskMachine"
        :machine-error="taskMachineError"
        :starting="taskStarting"
        :start-error="taskStartError"
        :action-error="taskActionError"
        :connected="roomConnected"
        :start="taskPage.start"
        :close="taskPage.close"
        :reopen="taskPage.reopen"
        :hand-over="taskPage.handOver"
        :rename="taskPage.rename"
        :set-collaborators="taskPage.setCollaborators"
        :load-machine="taskPage.loadMachine"
        :panel-open="panelOpen"
        @open-room="backToRoom"
        @toggle-panel="togglePanel"
      />
      <TopicHeader
        v-else
        :topic="selectedTopic"
        :members="store.members"
        :me="AUTHOR"
        :connected="roomConnected"
        :panel-open="panelOpen"
        @toggle-panel="togglePanel"
        @open-topic="openTopic"
        @rename="(title) => store.renameTopic(topicId, title)"
      />

      <!-- 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一刻。它自己决定
         什么时候出现（这一轮跑过一分钟、而且这个浏览器还没问过），平常什么都不
         画。放在这里而不是首屏：见组件自己的说明。 -->
      <PushPermissionPrompt :working="working" />

      <div v-if="taskId && !currentTask" class="task-state flex-grow-1">
        <v-progress-circular v-if="taskLoading" indeterminate color="primary" size="24" />
        <span v-else class="t-body c-muted">{{ taskLoadError ?? t('work.task.notFound') }}</span>
      </div>
      <!-- 手机：支线是一整页，← 回到频道。 -->
      <ThreadPane
        v-else-if="threadId && !mdAndUp"
        class="flex-grow-1"
        page
        :room="selectedTopic"
        :thread-id="threadId"
        :members="store.members"
        :topic-list="store.topics"
        :member-names="memberNames"
        @close="backToRoom"
        @open-task="onOpenCard"
        @to-task="handleUpgradeMessage"
        @open-file="(path: string) => panelRef?.openFile?.(path)"
        @open-topic="openTopic"
        @mention-click="handleMentionClick"
      />
      <div
        v-else
        ref="panesRef"
        :key="taskId ?? 'room'"
        class="panes d-flex flex-grow-1"
        style="min-width: 0; min-height: 0; position: relative"
      >
        <!-- 桌面：对话是左边那一栏。面板并排开着时两者之间有一条可拖的分隔，专注模式里
             这一栏像抽屉一样收起；面板收着或浮在上面时这一栏占满整宽。 -->
        <Transition name="focus-chat" @before-enter="freezeChatWidth" @before-leave="freezeChatWidth">
          <TopicChatColumn
            v-if="mdAndUp"
            v-show="!focusMode"
            ref="chatColumn"
            class="col col-chat"
            :style="chatStyle"
            :topic="selectedTopic"
            :members="store.members"
            :topic-list="store.topics"
            :unread-on-open="unreadOnOpen"
            :focus-block="focusBlock"
            :task-id="taskId ?? null"
            :composer-closed="composerClosed"
            :accept-elsewhere="focusMode"
            v-on="chatEvents"
            @open-room="backToRoom"
          />
        </Transition>
        <!-- 专注模式里对话收成这一条窄边：点它回到并排，有新回复时带一个点。 -->
        <div v-if="mdAndUp && focusMode" class="chat-rail">
          <BaseButton
            icon="mdi-message-outline"
            size="sm"
            :title="focusLabel"
            :aria-label="focusLabel"
            @click="toggleFocus"
          />
          <span v-if="chatNews" class="chat-rail__news" aria-hidden="true" />
        </div>
        <div
          v-if="mdAndUp && !focusMode && docked"
          class="pane-resizer"
          :title="t('work.topic.resize')"
          @mousedown.prevent="startPaneDrag"
          @dblclick="store.setChatPct(50)"
        />

        <!-- 面板浮层背后的遮罩。点它收起面板——和 Esc 同一条路。 -->
        <Transition name="panel-scrim">
          <div v-if="panelFloat && panelOpen" class="panel-scrim" @click="closePanel" />
        </Transition>

        <!-- 工作面板：并排时是右边那一栏，窄档里是从右边拉进来的浮层（三档见
             useTopicPanel）。浮层收起时 visibility 一并藏掉，不进 tab 序和读屏的树。 -->
        <div
          v-show="!mdAndUp || panelFloat || panelOpen"
          id="topic-panel"
          class="col col-doc panel-host"
          :class="{ 'panel-host--sheet': panelFloat, 'panel-host--open': panelFloat && panelOpen }"
          :style="panelFloat ? undefined : { flex: '1 1 0', minWidth: 0 }"
        >
          <!-- 桌面：支线占右边这一半。工作面板只是藏起来，关掉支线回来时还停在原来那一格。 -->
          <ThreadPane
            v-if="threadId && mdAndUp"
            :room="selectedTopic"
            :thread-id="threadId"
            :members="store.members"
            :topic-list="store.topics"
            :member-names="memberNames"
            @close="backToRoom"
            @open-task="onOpenCard"
            @to-task="handleUpgradeMessage"
            @open-file="(path: string) => panelRef?.openFile?.(path)"
            @open-topic="openTopic"
            @mention-click="handleMentionClick"
          />
          <WorkPanel
            v-show="!(threadId && mdAndUp)"
            ref="panelRef"
            :submit-question="submitQuestion"
            :agent-name="store.agentName"
            :agent-handle="store.agentHandle"
            :members="store.members"
            :activity="activity"
            :topic="selectedTopic"
            :task-id="taskId ?? null"
            :task-read-only="!!taskId && (!taskOwner || !taskOpen)"
            :activity-tick="activityTick"
            :working="working"
            :agent-control="agentControl"
            :site-turns="siteTurns"
            :topic-list="store.topics"
            :tab="panelTab"
            :card-phase="cardPhase"
            :with-chat="!mdAndUp"
            :compact="panelFloat || !panelOpen"
            :member-names="memberNames"
            :threads-new="channelThreads.hasNew.value"
            @open-topic="openTopic"
            @open-card="onOpenCard"
            @mention-click="handleMentionClick"
            @update:tab="onPanelTab"
            @locate="onLocate"
          >
            <!-- 页签栏右端：铺满（专注模式）和收起面板。只在桌面上并排的时候有。 -->
            <template v-if="mdAndUp && docked" #tab-actions>
              <BaseButton
                :icon="focusIcon"
                size="sm"
                :title="focusLabel"
                :aria-label="focusLabel"
                @click="toggleFocus"
              />
              <BaseButton icon="mdi-close" size="sm" :title="closeLabel" :aria-label="closeLabel" @click="closePanel" />
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
                :comparing="taskComparing"
                :comparison="taskComparison"
                :compare-error="taskCompareError"
                :checklist="taskOverview.checklist.value"
                :related="taskOverview.related.value"
                :can-retry="taskPage.takesPart.value"
                :retrying="taskOverview.retrying.value"
                :retry-error="taskOverview.retryError.value"
                @toggle-compare="taskPage.toggleCompare"
                @open-topic="openTopic"
                @mention-click="handleMentionClick"
                @retry-opening="taskOverview.retryOpening"
                @open-output="(path: string) => panelRef?.openFile?.(path)"
                @open-file="(path: string) => panelRef?.openFile?.(path)"
                @open-document="(id: string, title: string) => panelRef?.openDocument?.({ id, title })"
                @open-discussion="openDiscussion"
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
                @open-task="onOpenCard"
                @open-all="router.push({ name: 'project-tasks', params: { projectId }, query: { channel: topicId } })"
                @mention-click="handleMentionClick"
              />
            </template>
            <template v-if="!taskId" #threads>
              <PanelThreads
                :rows="channelThreads.rows.value"
                :loading="channelThreads.loading.value"
                :error="channelThreads.error.value"
                :refs="{ mentionNames: memberNames, topicTitles: {} }"
                :name-of="(handle: string) => memberNames[handle] || handle"
                :fmt-time="threadTime"
                @open="showThread"
                @open-task="onOpenCard"
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
                :composer-closed="composerClosed"
                v-on="chatEvents"
                @open-room="backToRoom"
              />
            </template>
          </WorkPanel>
          <!-- 专注模式里对话让开了，采纳那一条跟着到面板底部：要做的决定不能跟着消失。 -->
          <TopicAcceptCard
            v-if="mdAndUp && focusMode && selectedTopic"
            ref="panelAcceptRef"
            docked
            :topic-id="selectedTopic.id"
            :task-id="taskId ?? undefined"
            :topic-status="selectedTopic.status"
            @phase="(p: CardPhase) => (cardPhase = p)"
            @review="onReview"
          />
        </div>
      </div>
    </template>
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
.task-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.col {
  min-width: 0;
}
/* 专注模式：对话栏是一只侧抽屉（§9：整块进出 --dur-slow，走掉快一档）。动的是
   flex-basis；`!important` 是为了压过模板上那条内联的 `flex: 0 0 N%`。 */
.focus-chat-enter-active,
.focus-chat-leave-active {
  overflow: hidden;
}
.focus-chat-enter-active {
  transition:
    flex-basis var(--dur-slow) var(--ease-out),
    opacity var(--dur-slow) var(--ease-out);
}
.focus-chat-leave-active {
  transition:
    flex-basis var(--dur-base) var(--ease-in),
    opacity var(--dur-base) var(--ease-in);
}
.focus-chat-enter-from,
.focus-chat-leave-to {
  flex-basis: 0% !important;
  opacity: 0;
}
.focus-chat-enter-active > :deep(*),
.focus-chat-leave-active > :deep(*) {
  width: var(--chat-frozen-w);
  min-width: var(--chat-frozen-w);
}
/* 对话和面板之间那条可拖的线。看得见的只有 1px，和页面上别的分隔线一样重；能抓
   的范围左右各多 4px（::before），不然一条细线很难按准。它原来是一条 5px 的灰带，
   比屏幕上任何一条线都粗，悬停还变琥珀——琥珀留给主操作。 */
.chat-rail {
  display: flex;
  position: relative;
  flex: 0 0 44px;
  flex-direction: column;
  align-items: center;
  padding-top: 8px;
  border-right: 1px solid var(--line);
}
/* 对话收着时来了新回复：未读点（琥珀只给未读，§1.6）。 */
.chat-rail__news {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  pointer-events: none;
}
.pane-resizer {
  position: relative;
  z-index: var(--z-raised);
  flex: 0 0 1px;
  cursor: col-resize;
  background: var(--line);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.pane-resizer::before {
  content: '';
  position: absolute;
  inset: 0 -4px;
}
.pane-resizer:hover {
  background: var(--faint);
}

/* 工作面板的外壳（里面就是 WorkPanel 本身）：并排时是普通的 flex 子项，窄档里被
   下面的 --sheet 改成一只浮层。 */
.panel-host {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  min-height: 0;
}

/* 面板浮层：从对话右边拉进来的一张纸。收起时走进屏幕右侧并藏掉 visibility（不占
   tab 序、不进读屏的树）；visibility 的过渡延迟等于位移时长，收起时等位移走完才藏。 */
.panel-host--sheet {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: var(--z-panel-2);
  width: min(520px, 100%);
  background: var(--surface);
  border-left: 1px solid var(--line);
  box-shadow: var(--shadow-2);
  transform: translateX(100%);
  visibility: hidden;
  transition:
    transform var(--dur-base) var(--ease-standard),
    visibility 0s linear var(--dur-base);
}
.panel-host--open {
  transform: none;
  visibility: visible;
  transition:
    transform var(--dur-base) var(--ease-out),
    visibility 0s;
}

/* 浮层背后的遮罩：点它就和按 Esc 一样收起面板。 */
.panel-scrim {
  position: absolute;
  inset: 0;
  z-index: var(--z-panel);
  background: var(--overlay);
}
.panel-scrim-enter-active,
.panel-scrim-leave-active {
  transition: opacity var(--dur-base) var(--ease-standard);
}
.panel-scrim-enter-from,
.panel-scrim-leave-to {
  opacity: 0;
}

/* 用户关掉了动画就别拉。 */
@media (prefers-reduced-motion: reduce) {
  .panel-host--sheet,
  .panel-host--open,
  .panel-scrim-enter-active,
  .panel-scrim-leave-active {
    transition: none;
  }
}
</style>
