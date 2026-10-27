<script setup lang="ts">
// 工作面板: the right-hand half of a topic — 平级 tabs, 概览 / 现场 / 改动 / 预览 /
// 例行事务. It replaces the old 「文档 + 五个按需滑出的抽屉」 (预览/Git/现场/文件/资源):
// the drawers' float/pinned duality, their own width slider and the scrim are
// gone, and 资源 is no longer a panel at all — its numbers live in the topic
// header's usage popover.
//
// This component owns exactly two things, and deliberately nothing else:
//
//   1. WHICH tab is on screen, and WHICH tabs exist at all. Everything a tab
//      renders or fetches belongs to that tab's own SFC, so the four follow-up
//      cards each edit one file.
//   2. SIGNALS — facts about a tab that have to be right while that tab is
//      CLOSED (预览 has new content; how many files 改动 would show). They are
//      here rather than in the tabs because a closed component reports nothing.
//
// The one cross-tab wire is `open-file`: a <&path> chip in the doc (or in the
// chat, via `openFile`) opens that file where it lives — its own tab in the
// free zone when it is a room file the preview can draw, a read-only tab of the
// project's code when no task worktree holds it, the 改动 tab otherwise.
//
// 页签分两段。固定区（概览 / 现场 / 改动 / 预览 / 例行事务）不能关，位置记忆来自这里。自由区是
// 读者自己打开的那几份文件，可以关——变化是他自己做的，所以不算「页签自己出现和
// 消失」。单击打开的那一格是临时的，下一次打开会换掉它；双击就固定下来。不这样的
// 话，聊一小时能攒出二十个页签。
import type { ConversationFile } from '../composables/useConversationFiles'
import type { OpenFileTab } from '../composables/useTopicMemory'
import type { AgentControlState, Block, ProjectMemberRow, Topic } from '../cx_types'
import type { DocReviewRequest, OpenedDocument } from '../lib/docReview'
import type { MemberActivityLine } from '../lib/memberActivity'
import type { PreviewLocate, SubmitPreviewQuestion } from '../lib/previewQuestion'
import type { CardPhase } from '../lib/topicState'
import type { TabDef, TabKey } from './panels/panelTabList'
import type PanelDocHostComponent from './work/PanelDocHost.vue'

import { computed, defineAsyncComponent, nextTick, ref, useId, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import { useQuery } from '@tanstack/vue-query'

import { usePanelSplit } from '../composables/usePanelSplit'
import { useRoomFileProbe } from '../composables/useRoomFileProbe'
import { useTopicMemory } from '../composables/useTopicMemory'
import { useTopicWorkSummary } from '../composables/useTopicWorkSummary'
import { previewCanShowInRoom } from '../lib/fileKind'
import { whenIdle } from '../lib/idle'
import { withViewTransition } from '../lib/viewTransition'

import ErrorBoundary from './common/ErrorBoundary.vue'
// 这一屏有哪几格（共用表 + 只有产品有的「例行事务」）。这个文件里 `panelTabs` 已经
// 是「页签条要的那份数据」了，所以从 `workPanelTabs` 取。
import { workPanelTabs } from './panels/panelTabList'
import PanelTabs, { type PanelTab } from './panels/PanelTabs.vue'
import { confirmAnnotationDiscard } from './panels/preview/annotationDiscard'
import { usePreviewPointer } from './panels/preview/usePreviewPointer'
import RoomFilesPanelHost from './room/RoomFilesPanelHost.vue'
import RoutinePanelHost from './routine/RoutinePanelHost.vue'
import PanelChangesHost from './work/PanelChangesHost.vue'
import PanelPreviewHost from './work/PanelPreviewHost.vue'
import PanelSiteHost from './work/PanelSiteHost.vue'
import ProjectFileTab from './ProjectFileTab.vue'

import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { heldPreview, openRoomTasksQuery } from '@/query/room'

// 文档编辑器那一格（`PanelDocHost` 连着 tiptap + prosemirror + 文档 schema，压缩后约
// 1.1 MB）跟着这一屏的打开一起下来过：这一栏是 `TopicView` 静态引入的，而它把那一坨
// 静态引入进来，于是每个房间的首屏都背着一份点开文档才用得上的编辑器。文档那一格只有
// 真开了文档、`mounted` 里才有它，所以按需取就够了。
const PanelDocHost = defineAsyncComponent(() => import('./work/PanelDocHost.vue'))

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // 画的是这个房间里的一个任务：现场、改动、预览都是这个任务的，总览由 `overview`
    // 插槽填（任务的实况文档），没有「例行事务」。
    taskId?: string | null
    // 任务页上，改动只读（不是负责人，或任务已关）。
    taskReadOnly?: boolean
    // 这件任务有没有自己的分支。没有的（还没有电脑打开过它）没有工作区，「改动」那一格
    // 打不开它，文件就是项目现在的样子。
    taskHasBranch?: boolean
    submitQuestion?: SubmitPreviewQuestion
    // Bumped by the parent on AI activity (turn-done / a platform resource the
    // turn changed) so 文档 reloads the doc 芝士 just wrote. See TopicView
    // activityTick.
    activityTick: number
    // 芝士 正在这个话题里干活 —— tab 栏据此给「现场」加一个跳动的点。
    working?: boolean
    // 会话状态的最近一帧，一路透传给现场那格的会话详情。
    agentControl?: AgentControlState | null
    // 正在跑的轮次各自的开始时间（毫秒），一路透传给现场那格：哪一组还在进行。
    siteTurns?: Record<string, number>
    // Project topics: 文档 resolves <#id> chips with it.
    topicList?: Topic[]
    // Which tab the URL asks for (`?tab=`). The address is the page's business,
    // so TopicView owns it and this component only reports its own moves — that
    // keeps the panel mountable without a router, which is how its four suites
    // exercise it. An unknown or absent value leaves the choice here.
    tab?: string
    // 一句话里那个规则名字链接（`?routine=`）指名的那一条：切到「例行事务」那一格之
    // 后，那一格选中它、展开它、滚到它跟前。地址同样是 TopicView 的，这一栏只往下传。
    routineId?: string | null
    // 采纳卡处在哪一段（还没答 = undefined）。Only used, with `working`, to pick
    // which tab a topic OPENS on, and only when the address named none — after
    // that it is the reader's choice.
    cardPhase?: CardPhase
    // 手机上对话不是左边那一栏，是这条 tab 栏的第一格——一屏放不下两栏，而这两
    // 样东西本来就是平级的。开着它的时候 `chat` 插槽就是这一格的内容。
    withChat?: boolean
    // 平板横放（960–1180）：这一档里房间只画对话，面板是一只按需拉起的浮层。进房间时
    // 自动挑中的那一格（芝士在干活 → 现场，卡等你验收 → 改动）留在面板里当「你打开时
    // 看哪一格」，但不写地址、也不把浮层拉起来——那是「你打开它」，不是「有人打开了
    // 这一格」。宽档里面板常驻、手机上又是另一套（`withChat`），都不经过这里。
    compact?: boolean
    // 房间名册 handle → 名字。现场那一格用它给每一行署名。一路透传：漏掉它不
    // 报错，只是那一格里写的是 handle。
    memberNames?: Record<string, string>
    /** 项目 AI 队友的名字（项目可以给它改名），提示和空态里用它，不写死「芝士」。 */
    agentName?: string
    /** 项目 AI 队友的 handle：文档评论里「问…」点的是它。 */
    agentHandle?: string | null
    /** 项目名册：文档里 @ 得到的人。 */
    members?: ProjectMemberRow[]
    // 此刻谁在这个房间里忙（对话栏从 socket 上学来）。现场那一格画其中在干活的队友。
    activity?: MemberActivityLine[]
    // 「支线」那一格里有我没读过的回复：页签上挂一个点。
    threadsNew?: boolean
  }>(),
  {
    taskId: null,
    taskReadOnly: false,
    taskHasBranch: true,
    submitQuestion: undefined,
    working: false,
    agentControl: null,
    siteTurns: () => ({}),
    topicList: () => [],
    memberNames: () => ({}),
    tab: undefined,
    routineId: null,
    cardPhase: undefined,
    withChat: false,
    compact: false,
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    members: () => [],
    activity: () => [],
  }
)

const emit = defineEmits<{
  /** `blockId`：要定位到的那条消息（「被引用」里点的一条对话）。 */
  (e: 'open-topic', topicId: string, blockId?: string): void
  (e: 'open-card', taskId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'update:tab', key: string): void
  // 预览面板里读者指着文档说的那一句，交给拿着对话的那一层；图上画过东西时
  // 随行带那张合成图。
  (e: 'locate', payload: PreviewLocate): void
}>()

// 有哪几格、各叫什么、挂哪个图标在 `panels/panelTabList.ts`：文档里的演示照着
// 同一份表画这条栏，一个名字只有一个出处。
// 地址没指定、阶段也没话说的时候落在哪一格：手机上是对话（你进话题多半是来说话
// 的），桌面上对话就在旁边那一栏，所以是总览。
const defaultTab = computed<TabKey>(() => (props.withChat ? 'chat' : 'overview'))
// 旧地址还带着 ?tab=doc / ?tab=tasks —— 两个 tab 都并进概览了，所以它们指的就是
// 概览。链接不该因为我们合并了界面而失效。
const TAB_ALIASES: Record<string, TabKey> = { doc: 'overview', tasks: 'overview' }
// ---- 自由区 ----
// 一份文件一个页签，键是 `file:<路径>`，地址里的 `?tab=` 用的也是它——「你看一下
// 这份报告」得是一条能发出去的链接。
const FILE_TAB = 'file:'
// 自由区里的资料库文档，路径写成 `doc:<编号>`：和文件同一排页签，打开的是文档。
const DOC_TAB = 'doc:'
function fileKey(path: string): string {
  return FILE_TAB + path
}
const openFiles = ref<OpenFileTab[]>([])
// 自由区属于房间：切去别的房间再回来，开着的那几份还在。只记在这一次会话里。
const { filesByTopic } = useTopicMemory()
// 自由区那一格是从来处开出来的，关掉最后一份时回到来处——你点开这份文件之前在做的
// 那件事（手机上是对话），比总览更像你要回去的地方。连着开几份只记第一次：回到来处
// 说的是回到你离开的固定页签，不是退回上一份文件（那是旁边那一格的事）。
const fileOrigin = ref<string | null>(null)
function noteFileOrigin() {
  if (!active.value.startsWith(FILE_TAB)) fileOrigin.value = active.value
}

const active = ref<string>(defaultTab.value)
/** The URL's answer, if it names a tab that exists (or one that used to). */
function tabFromUrl(): string | null {
  const asked = props.tab
  if (!asked) return null
  if (asked.startsWith(FILE_TAB) && asked.length > FILE_TAB.length) return asked
  if (tabs.value.some((t) => t.key === asked)) return asked as TabKey
  return TAB_ALIASES[asked] ?? null
}
/** 地址点名了自由区的一份文件，而它还没开着：照着地址开出来（临时位）。 */
function ensureFileFromUrl(key: string | null) {
  if (!key?.startsWith(FILE_TAB)) return
  const path = key.slice(FILE_TAB.length)
  if (openFiles.value.some((f) => f.path === path)) return
  // 资料库文档的页签：名字等它自己读到了再补上。
  if (path.startsWith(DOC_TAB)) placeFile(path, { id: path.slice(DOC_TAB.length), title: '' })
  else placeFile(path)
}

// 窄屏上这条栏会横向滚动，所以「哪一格是选中的」和「你看得见哪一格」不再是同一
// 件事：阶段自动选中的那一格（比如开工时的现场）可能整个在屏幕外，屏幕上什么都
// 没发生。把选中那一格带回视野、以及那条下边线的量法，都在 `PanelTabs` 里——它
// 是这条栏的组件，产品页和文档里的演示共用它。

// Every move the panel makes goes through here, so the address always says what
// is on screen — 「你来看一眼这个 diff」的链接成立的前提就是这个。
//
// 换页签会离开图片那格，图上没发出去的标注就跟着没了，所以先问一句（确认不了就
// 留在原地）。切工具不算——那件小事不经过这里。
//
// `guard: false` 是给「不是用户主动离开图」的入口用的：`pulse` / `highlightTurn` /
// `reviewDoc` 是聊天里点「查看改动 / 看这一轮」掀开总览，图那格用 `v-show` 留着、笔画
// 不会丢——拦住它们只会平白弹一次框，再把这次点击变成一次没落地的空操作。
//
// 返回「到底切没切」：调用方要接着在目标那一格上做事（开文件）时，被拦下就得当场
// 放弃，不能拿着旧的引用假装做过了。
//
// `announce: false` 是「换这一格，但别告诉地址」：平板横放里进房间时自动挑中的那一格
// 就是这样——它只是「你打开面板时看哪一格」，写进地址等于把浮层也拉起来了，而那一刻
// 并没有人打开它。（宽档、手机上都用默认的 announce —— 那里地址本来就该跟着走。）
async function setTab(key: string, opts: { guard?: boolean; announce?: boolean } = {}): Promise<boolean> {
  if (key !== active.value && opts.guard !== false && !(await confirmAnnotationDiscard())) return false
  settled.value = true
  active.value = key
  if (key === 'changes') markChangesSeen()
  if (opts.announce !== false) emit('update:tab', key)
  return true
}

// 人点了一格页签：宽屏上内容区淡入淡出一下（lib/viewTransition.ts；窄屏走上面的
// tabpane-in）。确认「放弃没发的批注」要在过渡之前问完，过渡里不能等人。
async function selectTab(key: string) {
  if (key === active.value) return
  if (!(await confirmAnnotationDiscard())) return
  withViewTransition(() => setTab(key, { guard: false }))
}

// ---- 开在哪个 tab 上 (规则 3) ----
// Opening a topic is the one moment choosing a tab is not snatching the view,
// so it is the one moment the panel gets to choose: 芝士 干着活的时候你多半是来
// 看它在干什么的，卡等你验收的时候你是来看它干了什么的。
//
// `settled` is what keeps it to that moment. The card's phase arrives asynchronously —
// the accept card has to load before anyone knows a card is pending, and until
// it has the prop is undefined rather than 「没有卡」 — so this cannot run when the
// topic opens. It runs on the first phase this topic reports, and never again
// (another topic gets its own panel).
const settled = ref(false)

// 只挑有东西可看的那一格：挑中一格空的，人一进房间看到的就是一句「暂无」——
// 待验收的房间没有改动文件（比如项目还没接仓库）时，原来就落在一块报错上。
function openingTab(card: CardPhase): TabKey {
  if (!props.taskId) return defaultTab.value
  if (props.working) return 'site'
  if (card && hasContent('changes')) return 'changes'
  return defaultTab.value
}

// Back / forward, or someone pasting a link into the open topic. 手机上对话那一格
// 的地址可以不带 `?tab=`（刚进房间时就是这样），退回到它时也得回到对话。
watch(
  () => props.tab,
  () => {
    const asked = tabFromUrl() ?? (props.withChat ? defaultTab.value : null)
    ensureFileFromUrl(asked)
    if (asked && asked !== active.value) active.value = asked
  }
)

// 地址里点名了某条规则（`?routine=`）：那一格得先开着，才轮得到它自己去选中。地址一
// 般连 `tab=routines` 一起带，这里兜的是只剩 `routine` 的链接——不用问人、也不丢东西。
watch(
  () => props.routineId,
  (id) => {
    // 任务页上没有这一格：走错门就别硬切。
    if (id && !props.taskId && active.value !== 'routines') active.value = 'routines'
  },
  { immediate: true }
)

// A tab mounts the first time it is selected and then stays mounted — which is
// what the drawer effectively did with its state (openPath, expanded folders,
// the transcript all survived a close/open). 文档 is mounted from the start
// because it is the default tab and its editor is expensive to rebuild.
const mounted = ref<Set<string>>(new Set<string>([active.value]))
function show(k: string) {
  if (!mounted.value.has(k)) mounted.value = new Set(mounted.value).add(k)
}
watch(active, show)

// ---- 手机上换页签时内容从哪边进来 ----
// 一屏只有一格，换页签时新的那一格从它页签所在的方向挪进来（右边的页签从右边来），
// 人看得出自己是往哪边走了。动的只是进来的那一格的外层：各格一直挂着（对话的滚动
// 位置、键盘弹起时的贴底都在里面），不为了演一下重建。桌面上两栏并排，不演。
const tabOrder = computed(() => [
  ...tabs.value.map((tab) => tab.key as string),
  ...openFiles.value.map((f) => fileKey(f.path)),
])
const entering = ref<{ key: string; from: 'left' | 'right' } | null>(null)
watch(active, (now, before) => {
  if (!props.withChat) return
  const order = tabOrder.value
  entering.value = { key: now, from: order.indexOf(now) < order.indexOf(before) ? 'left' : 'right' }
})
function enterClass(key: string) {
  const e = entering.value
  const side = split.value === key ? 'tabpane--side ' : ''
  return e?.key === key ? `${side}tabpane-in tabpane-in--${e.from}` : side || undefined
}

const changesRef = ref<InstanceType<typeof PanelChangesHost> | null>(null)

const topicId = computed(() => props.topic?.id ?? null)
// 这一面板读的那段对话：任务页上是任务，否则是房间自己。
const conversationId = computed(() => props.taskId ?? topicId.value)
const projectId = computed(() => props.topic?.project_id ?? null)

// A turn just ended: that is the moment 芝士's commits, its working tree and
// whatever it pointed the preview at actually changed. One tick, so each tab
// decides for itself what to re-fetch — no tab needs to watch `working`.
const refreshTick = ref(0)
watch(
  () => props.working,
  (now, before) => {
    if (!before || now) return
    refreshTick.value += 1
    // 预览和改动只有任务有（见 CHANNEL_TABS）。
    if (props.taskId) {
      void pollPreviewPointer()
      void pollWorkSummary()
    }
    // 一轮里派出去的活，收工那一刻就该出现在 任务 那一格上。
    pollThreads()
  }
)

// ---- 「有新内容」 on the 预览 tab ----
// 芝士摆出来的东西不是一条消息，是一个指针（它指着这个房间此刻该看的那一份），而摆出
// 来时「预览」那一格多半没开着——所以这颗点、它问的指针、和那圈兜底轮询是一件事，都
// 在 `panels/preview/usePreviewPointer` 里。这里只要那几个名字。
const {
  path: previewPath,
  latest: previewLatest,
  hasNew: previewHasNew,
  markSeen: markPreviewSeen,
  poll: pollPreviewPointer,
  previewShown,
} = usePreviewPointer({
  conversationId,
  active,
  refreshTick,
  // 预览和改动只有任务有（见 CHANNEL_TABS）：频道没有电脑在它名下干活。
  taskScoped: computed(() => !!props.taskId),
})

// ---- 这一格此刻有没有东西 ----
// 四格永远都在、位置不变：人记得住「改动在第三格」，一格时有时无的 tab 栏两次打开
// 可能不一样长。没东西的那一格字变浅，点进去是它自己的「暂无」。这里只回答有没有，
// 给字的深浅用，也给「开在哪一格」用——那一刻不该挑一格空的。
//
// 这份摘要（取数、「有没有新改动」、换话题清不清空）在
// `composables/useTopicWorkSummary.ts` 那一层：组件不直接碰 API 层。
const {
  summary,
  loaded: summaryLoaded,
  hasNew: changesHasNew,
  markSeen: markChangesSeen,
  poll: readWorkSummary,
} = useTopicWorkSummary({ topicId: conversationId, projectId })

async function pollWorkSummary(opts: { seen?: boolean } = {}) {
  // A failed poll is not a state: what the tabs show stays as it was. It still
  // counts as an answer for 「开在哪一格」, which then stays on the default tab
  // rather than waiting on a summary that may never come.
  if (!(await readWorkSummary())) return
  // Arriving at a topic that already had changes is not news, exactly as it is
  // not news for the preview — the hint means 「这一轮干出来的」.
  if (opts.seen || active.value === 'changes') markChangesSeen()
}

// ---- 这个房间派出去了几件活 ----
// A signal, so 总览 can carry its count while closed and can stay out of the way
// of a room that never dispatched anything.
// 只取开着的活本身，不带对话：这里只是为了数一数有几条开着。和频道概览读同一份，
// 切回房间时上次那个数先在。
const openTasksRead = useQuery(
  computed(() => ({ ...openRoomTasksQuery(topicId.value ?? ''), enabled: !!topicId.value && !props.taskId })),
  queryClient
)
// 任务页上答 0（`threads` 是 `signalFor` 和 `tabTitle` 的唯一出处）：这一份属于房间，
// 不属于画在它里面的任务，而它会漏进来——这一格和频道概览读同一份缓存，`enabled` 挡不住。
const threads = computed<{ open: number }>(() => ({
  open: props.taskId ? 0 : (openTasksRead.data.value?.data ?? []).filter((r) => r.status === 'open').length,
}))

function pollThreads() {
  const roomId = props.topic?.id
  if (!roomId || props.taskId) return
  void queryClient.invalidateQueries({ queryKey: keys.roomTasks(roomId) })
}

function hasContent(key: TabKey): boolean {
  if (key === 'chat' || key === 'overview' || key === 'threads' || key === 'files') return true
  // 「例行事务」也是永远有得看的一格：没有例行事务时它写的是「还没有例行事务，点新建」——
  // 那一格自己是让人动手建一条的地方，不是一个「暂无」。数有几条要现问后端，而这一格
  // 关着的时候不该为此多打一个请求。
  if (key === 'routines') return true
  // 改动属于**树**：一棵树 = 一个分支 = 一个 PR = 一批活，所以这份 diff 是这个
  // 房间当前这一批一起写出来的。
  if (key === 'changes') return summary.value.changedFiles.length > 0
  // 现场 is where 芝士 works: it has something once the room has run, and from the
  // first moment of the first turn (before the session id is captured).
  if (key === 'site') return summary.value.hasRun || props.working
  return !!previewLatest.value
}

// 频道是人说话的地方，没有电脑在它名下干活：只有概览、支线、文件、例行事务。任务没有
// 支线、文件和例行事务，其余几格都有。
const CHANNEL_TABS: ReadonlySet<string> = new Set(['chat', 'overview', 'threads', 'files', 'routines'])
const TASK_HIDDEN_TABS: ReadonlySet<string> = new Set(['routines', 'threads', 'files'])
const tabs = computed(() =>
  workPanelTabs(props.withChat).filter((tab) =>
    props.taskId ? !TASK_HIDDEN_TABS.has(tab.key) : CHANNEL_TABS.has(tab.key)
  )
)
// 命令面板里「切到总览」这样的操作：页签有哪几格，这里说了算。
useCommands(() =>
  tabs.value.map((tab) => ({
    id: `room.tab.${tab.key}`,
    title: t('navigation.palette.showTab', { tab: tab.label }),
    icon: tab.icon,
    run: () => setTab(tab.key),
  }))
)

/** What the signal on a tab means, for people who reach it by hover or reader. */
function tabTitle(tab: TabDef): string {
  const detailed = (detail: string) => t('work.room.panel.tabDetail', { label: tab.label, detail })
  const pair = (first: string, second: string) => t('work.room.panel.detailPair', { first, second })
  if (tab.key === 'overview' && threads.value.open) {
    return detailed(t('work.room.panel.inProgress', { count: threads.value.open }))
  }
  if (tab.key === 'preview' && previewHasNew.value) return detailed(t('work.room.panel.newContent'))
  if (tab.key === 'changes' && summary.value.changedFiles.length) {
    const files = t('work.room.panel.fileCount', { count: summary.value.changedFiles.length })
    return detailed(changesHasNew.value ? pair(files, t('work.room.panel.newChanges')) : files)
  }
  return tab.label
}

/** 挂在页签上的那个信号。哪一格挂什么属于工作面板的账，`PanelTabs` 只负责画。 */
function signalFor(key: TabKey): PanelTab['signal'] {
  if (key === 'site' && props.working) return { kind: 'pulse' }
  if (key === 'preview' && previewHasNew.value) return { kind: 'dot' }
  if (key === 'threads' && props.threadsNew) return { kind: 'dot' }
  // 进行中的才数：关掉的再多也不是这个频道现在在忙的事。
  if (key === 'overview' && threads.value.open) return { kind: 'count', count: threads.value.open }
  if (key === 'changes' && summary.value.changedFiles.length) {
    return { kind: 'count', count: summary.value.changedFiles.length, fresh: changesHasNew.value }
  }
  return undefined
}

/** 交给 `PanelTabs` 的那几格：文案、图标、有没有东西、信号。 */
// 页签和它切换的内容区（role="tabpanel"）靠这个 id 连起来：读屏在页签上念得出它管哪一块。
const tabPanelId = `wp-panel-${useId()}`
// 内容区在错误边界里面：某一格渲染出错时它会被换成兜底提示，那时页签不再指向它。
const tabBodyRef = ref<HTMLElement | null>(null)
const { width: bodyWidth } = useElementSize(tabBodyRef)
const keyExists = (key: string) =>
  tabs.value.some((tab) => tab.key === key) || openFiles.value.some((f) => fileKey(f.path) === key)
const {
  split,
  canSplit,
  toggle: toggleSplit,
  shown,
  drop: dropSplit,
} = usePanelSplit(active, { width: bodyWidth, phone: computed(() => props.withChat), exists: keyExists })
const activeTabLabel = computed(() =>
  active.value.startsWith('file:')
    ? active.value.slice('file:'.length).split('/').pop()
    : panelTabs.value.find((tab) => tab.key === active.value)?.label
)

const panelTabs = computed<PanelTab[]>(() =>
  tabs.value.map((tab) => ({
    key: tab.key,
    label: tab.label,
    icon: tab.icon,
    empty: !hasContent(tab.key),
    title: tabTitle(tab),
    signal: signalFor(tab.key),
  }))
)

// Opening the topic: the address decides, 文档 when it says nothing. Baseline the
// dot against whatever this topic already had, so opening a topic — including
// straight onto 预览 from someone's link — never greets you with a hint for work
// that was there before you arrived.
{
  const id = conversationId.value
  openFiles.value = (id && filesByTopic.get(id)) || []
  const asked = tabFromUrl()
  ensureFileFromUrl(asked)
  active.value = asked ?? defaultTab.value
  // 「URL 里显式带 ?tab= 时以 URL 为准」: an address that names a tab has already
  // decided, so the phase does not get to.
  settled.value = !!asked
  markPreviewSeen(null)
  // 预览和改动两格只有任务有（频道没有电脑在它名下干活，见 CHANNEL_TABS）：频道不去问
  // 它们。频道「没有改动」是事实，不用等谁来答。
  if (!props.taskId) summaryLoaded.value = true
  if (id && props.taskId) {
    // 这件任务的当前预览，之前问过的还在缓存里（`query/room`）：命中就直接用——面板
    // 挂上来时那份答案就在手边，不必再等一轮网络。没命中才自己问。
    const warm = heldPreview(id)
    if (warm !== undefined) {
      previewPath.value = warm?.path ?? null
      markPreviewSeen(warm?.artifact_id ?? null)
    } else {
      void pollPreviewPointer({ seen: true })
    }
    // 这一条要等服务端算（几秒），而它只决定「改动」那一格的深浅、以及该开在哪一格。
    // 推到首屏画完、浏览器空下来再问：它不该和真正要把内容画出来的那些请求抢同一条
    // 网络和主线程。角标随后补上，逻辑不受影响（`summaryLoaded` 那只看的是有没有回过）。
    const openedId = id
    whenIdle(() => {
      if (conversationId.value === openedId) void pollWorkSummary({ seen: true })
    })
  }
}

// Declared after the opening block on purpose: it fires immediately on mount and
// must see the `settled` that block sets.
//
// 待验收 / 交付中要等 summary 回来才挑：开在「改动」的前提是真有改动，而两个请求
// 同时发出，谁先到说不准。等到的是一个事实，不是一场赛跑。
watch(
  [() => props.cardPhase, summaryLoaded],
  ([card, loaded]) => {
    if (settled.value || card === undefined) return
    // 手机上房间永远开在对话：输入框就在那一格里，自动跳去现场等于把它藏起来。
    // 现场那一格上的呼吸点照样说着「正在工作」。
    if (props.withChat) {
      settled.value = true
      return
    }
    if (!props.working && card && !loaded) return
    const want = openingTab(card)
    if (want === active.value) settled.value = true
    // 平板横放：挑中的那一格留着当「打开时看哪一格」，但不写地址（于是也不拉开浮层）。
    else setTab(want, { announce: !props.compact })
  },
  { immediate: true }
)

// ---- The panel's outward API (TopicView holds a ref) ----
// 「把总览掀到眼前」（聊天里点了「查看改动 / 看这一轮」）：不是用户主动离开正在标注
// 的那张图，绕过守卫切过去。总览里是什么由 TopicView 填，掀开之后的那一下也由它做。
async function showOverview() {
  await setTab('overview', { guard: false })
  await nextTick()
}
// A chip is a path with no store, and a room has three: its own files (what 芝士
// delivered and what people uploaded — no branch, no history), a task's worktree,
// and the project's current code. So find the file FIRST and pick the tab from
// where it turned out to be. Done the other way round, a reader who clicks a file
// 芝士 just made gets the 改动 tab appearing out of nowhere, a listing that does
// not contain it, and then a read error on top — the file was never in a tree.
async function openFile(path: string) {
  // A chip may carry the lines it was pointing at (`src/a.ts:12-30`) — that part
  // names a place inside the file, not a file, and neither store knows it.
  const at = path.match(/:(\d+)(?:-(\d+))?$/)
  const want = at ? path.slice(0, at.index) : path
  // 「房间文件」这一档包含网页：内容域按路径服务房间里的任意一份，所以一份 html
  // 在这里画得出来。仓库树里的 .html 不在此列（那不是房间文件）。
  if (previewCanShowInRoom(want) && (await inRoomFiles(want))) {
    openFileTab(want)
    return
  }
  // 剩下的都是源文件，分两处：这件任务的工作副本里有它，就开在「改动」那一格；没有，
  // 就退回项目当前版本那一格只读地开它——频道里没有这一格，任务还没有分支（没有电脑
  // 打开过它）也算没有。一枚 chip 是读者指名要看的一份文件，不该因为没归到这件任务就
  // 点不开；当前版本里也没有它时，那一格自己会说，并列出改过它的任务。
  if (props.taskId && props.taskHasBranch) {
    // 画不出来的（跑着的应用）只剩预览那一格。它指着谁要现问：芝士一轮里摆出来的东西，
    // 这里手上那份记录要等这一轮结束才更新。
    await pollPreviewPointer()
    if (want === previewPath.value) {
      await setTab('preview')
      return
    }
    // 这一步是用户点了一份文件，该走守卫问一句。问不到「可以走」就当场收手：`setTab`
    // 停在原地，「改动」那一格要么是空的、要么指向一份没露面的「改动」——再往下走就是
    // 一次没有落地、也没人知道的假动作。
    if (!(await setTab('changes'))) return
    await nextTick()
    if (await changesRef.value?.openFile(want)) return
  }
  await openFileTab(want, { lines: at ? { start: Number(at[1]), end: Number(at[2] ?? at[1]) } : null })
}

// 这份文件是不是房间自己的（芝士交付的、人传上来的）。不是就去树上找。
const { exists: inRoomFiles } = useRoomFileProbe(topicId)

// 放进自由区，不切过去：开着的就不动，否则占临时位——有一格临时的就在原位换掉它，
// 没有就排到最后。
function placeFile(path: string, document?: { id: string; title: string }, project?: OpenFileTab['project']) {
  if (openFiles.value.some((f) => f.path === path)) return
  const next = [...openFiles.value]
  const tab: OpenFileTab = { path, pinned: false, ...(document ? { document } : {}), ...(project ? { project } : {}) }
  const temp = next.findIndex((f) => !f.pinned)
  if (temp >= 0) next.splice(temp, 1, tab)
  else next.push(tab)
  setFiles(next)
}

// 聊天里那张文档卡：资料库里的这份文档在自由区开一格，改过的一处处标出来。
const docRefs = new Map<string, InstanceType<typeof PanelDocHostComponent>>()
// 编辑器那一块是按需取的（那一头是 `defineAsyncComponent`，约 1.1 MB）：冷开时点「查看
// 改动」，面板已经切过去了，那一块还在网络上，ref 也就还没接上。这时候递过去会被 `?.`
// 静默吞掉——高亮不发生，也不报错。所以递不出去的先记在这儿，`keepDocRef` 接上再补一次。
const waitingReviews = new Map<string, DocReviewRequest>()
/** 「文件」那一格里点开一份：文档开成文档，附件按路径开。 */
function openConversationFile(row: ConversationFile) {
  if (row.kind === 'document') void openDocument({ id: row.id, title: row.name })
  else void openFile(row.id)
}

async function openDocument(document: OpenedDocument, review?: DocReviewRequest) {
  const path = DOC_TAB + document.id
  noteFileOrigin()
  placeFile(path, { ...document })
  if (!(await setTab(fileKey(path)))) return
  if (!review) return
  // 编辑器要等文档到了才找得到那几处；面板自己会等，这里只要它已经挂上。
  await nextTick()
  const host = docRefs.get(document.id)
  if (host) host.reviewEdits(review)
  else waitingReviews.set(document.id, review)
}
function keepDocRef(id: string, el: unknown) {
  if (!el) {
    docRefs.delete(id)
    // 那一格关掉了：没递出去的「查看改动」跟着作废，不然下一次开同一份时它会突然亮。
    waitingReviews.delete(id)
    return
  }
  const host = el as InstanceType<typeof PanelDocHostComponent>
  docRefs.set(id, host)
  const review = waitingReviews.get(id)
  if (review) {
    waitingReviews.delete(id)
    host.reviewEdits(review)
  }
}
function retitle(id: string, title: string) {
  setFiles(openFiles.value.map((f) => (f.document?.id === id ? { ...f, document: { ...f.document, title } } : f)))
}

// 自由区开一格。`project` 有值时开的是「项目当前版本」那一格：只读，带着 chip 指的那几行
// （没指就是 null）。
function openFileTab(path: string, project?: OpenFileTab['project']) {
  noteFileOrigin()
  placeFile(path, undefined, project)
  if (project) setFiles(openFiles.value.map((f) => (f.path === path ? { ...f, project } : f)))
  setTab(fileKey(path))
}

function pinFile(path: string) {
  setFiles(openFiles.value.map((f) => (f.path === path ? { ...f, pinned: true } : f)))
}

// 关掉的是正看着的那一格，就落到它旁边那一格；自由区空了就回到来处。
//
// 关掉正看着的那一格＝离开一块正在标注的图，所以先问一句，问完再动手拆。后台那几格
// （没在看）不会拦：它们本就登记不上（见 `annotationDiscard`）。
async function closeFile(path: string) {
  const at = openFiles.value.findIndex((f) => f.path === path)
  if (at < 0) return
  const key = fileKey(path)
  dropSplit(key)
  const wasActive = active.value === key
  if (wasActive && !(await confirmAnnotationDiscard())) return
  const next = openFiles.value.filter((f) => f.path !== path)
  setFiles(next)
  const nextMounted = new Set(mounted.value)
  nextMounted.delete(key)
  mounted.value = nextMounted
  if (!wasActive) return
  const neighbour = next[Math.min(at, next.length - 1)]
  if (neighbour) {
    setTab(fileKey(neighbour.path))
    return
  }
  // 来处可能已经不在了（这件任务还没有分支、换过房间），那就还是落到兜底那一格。
  const origin = fileOrigin.value
  setTab(origin && tabs.value.some((t) => t.key === origin) ? origin : defaultTab.value)
}

function setFiles(next: OpenFileTab[]) {
  openFiles.value = next
  const tid = conversationId.value
  if (tid) filesByTopic.set(tid, next)
}

// 对话栏的 socket 上来了现场的一行：交给现场那格。那格还没打开过就不用管，它第一次
// 打开时会整段读一遍。
const siteRef = ref<InstanceType<typeof PanelSiteHost> | null>(null)
function siteBlock(block: Block) {
  siteRef.value?.receive(block)
}

// 面板此刻在画哪一格。地址不一定写得出来——平板横放里自动挑中的那一格就没写进地址，
// 而收起浮层再打开要回到它，所以这里是那份记忆的出处（TopicView 打开浮层时来问）。
defineExpose({
  showOverview,
  openFile,
  openDocument,
  siteBlock,
  previewShown,
  activeTab: () => active.value,
})
</script>

<template>
  <div class="work-panel">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>{{ t('work.room.panel.pickTopic') }}</div>
      </div>
    </div>

    <template v-else>
      <!-- 这条栏是 `PanelTabs` 画的：产品页和文档里的动态演示共用同一个组件，演示
           的四格于是永远和这里长得一样。信号（谁在干活、有几个文件改了）和「哪一
           格此刻没东西」都由这里算好交给它。 -->
      <PanelTabs
        :tabs="panelTabs"
        :active="active"
        :files="openFiles"
        :phone="withChat"
        :panel-id="tabBodyRef ? tabPanelId : undefined"
        @select="selectTab"
        @close-file="closeFile"
        @pin-file="pinFile"
      >
        <template v-if="$slots['tab-actions'] || canSplit || split" #actions>
          <BaseButton
            v-if="canSplit || split"
            size="sm"
            :icon="split ? 'mdi-rectangle-outline' : 'mdi-view-split-vertical'"
            data-testid="panel-split"
            :aria-pressed="!!split"
            :title="split ? t('work.room.panel.unsplit') : t('work.room.panel.split')"
            :aria-label="split ? t('work.room.panel.unsplit') : t('work.room.panel.split')"
            @click="toggleSplit"
          /><slot name="tab-actions" />
        </template>
      </PanelTabs>

      <!-- Panel content (not the tab strip) gets its own boundary: a tab that
           throws shows the fallback here while the strip stays usable.
           resetKey = topic id, so switching topics recovers on its own. -->
      <ErrorBoundary variant="compact" :reset-key="topic.id">
        <div
          :id="tabPanelId"
          ref="tabBodyRef"
          class="tabbody"
          :class="{ 'tabbody--phone': withChat }"
          role="tabpanel"
          :aria-label="activeTabLabel"
        >
          <!-- 对话这一格由 TopicView 填（它拿着 ChatPanel 的那一堆接线）。一直挂着
               而不是切走就卸载：卸掉会断掉连接、丢掉滚动位置。 -->
          <div v-if="withChat" v-show="active === 'chat'" class="tabpane-chat" :class="enterClass('chat')">
            <slot name="chat" />
          </div>
          <div v-show="shown('overview')" class="tabpane-slot" :class="enterClass('overview')">
            <slot name="overview" />
          </div>
          <div v-if="!taskId" v-show="shown('threads')" class="tabpane-slot" :class="enterClass('threads')">
            <slot name="threads" />
          </div>
          <PanelSiteHost
            v-if="mounted.has('site')"
            v-show="shown('site')"
            ref="siteRef"
            :class="enterClass('site')"
            :agent-name="agentName"
            :topic-id="conversationId"
            :project-id="projectId"
            :active="shown('site')"
            :running-turns="siteTurns"
            :refresh-tick="refreshTick"
            :member-names="memberNames"
            :working="working"
            :activity="activity"
            :agent-control="agentControl"
            @open-file="openFile"
            @open-topic="(id: string, block?: string) => emit('open-topic', id, block)"
            @mention-click="emit('mention-click', $event)"
          />
          <PanelChangesHost
            v-if="mounted.has('changes')"
            v-show="shown('changes')"
            ref="changesRef"
            :class="enterClass('changes')"
            :topic-id="topicId"
            :task-id="taskId"
            :read-only="topic?.status === 'archived' || taskReadOnly"
            :project-id="projectId"
            :active="shown('changes')"
            :refresh-tick="refreshTick"
            :agent-name="agentName"
            :member-names="memberNames"
          />
          <PanelPreviewHost
            v-if="mounted.has('preview')"
            v-show="shown('preview')"
            :submit-question="submitQuestion"
            :class="enterClass('preview')"
            :topic-id="conversationId"
            :project-id="projectId"
            :active="shown('preview')"
            :refresh-tick="refreshTick"
            :agent-name="agentName"
            :members="members"
            :agent-handle="agentHandle"
            @loaded="markPreviewSeen"
            @locate="emit('locate', $event)"
            @open-file="openFileTab"
            @mention-click="emit('mention-click', $event)"
          />
          <!-- 这个房间的规则：到点或发生某件事时它自己开工。取数在新的一轮结束时跟一次
             （`refreshTick`）—— 芝士可能刚在房间里起草了一条。规则交回的结果文件点开
             在这一格里（`openFile` 先认它是不是房间自己的文件，再挑开哪一格）。 -->
          <RoomFilesPanelHost
            v-if="mounted.has('files')"
            v-show="shown('files')"
            :class="enterClass('files')"
            :topic-id="taskId ?? topicId"
            :seats-only="!!topic?.members_only"
            @open="openConversationFile"
          />
          <RoutinePanelHost
            v-if="mounted.has('routines')"
            v-show="shown('routines')"
            :class="enterClass('routines')"
            :topic-id="topicId"
            :project-id="projectId"
            :room-title="topic?.title ?? ''"
            :routine-id="routineId ?? null"
            :refresh-tick="refreshTick"
            :agent-name="agentName"
            @open-file="openFile"
          />
          <template v-for="f in openFiles" :key="fileKey(f.path)">
            <PanelDocHost
              v-if="f.document && mounted.has(fileKey(f.path))"
              v-show="shown(fileKey(f.path))"
              :ref="(el: unknown) => keepDocRef(f.document!.id, el)"
              :class="enterClass(fileKey(f.path))"
              :topic="null"
              :document="{ ...f.document, projectId: projectId ?? topic?.project_id ?? '' }"
              :activity-tick="activityTick"
              :agent-name="agentName"
              :agent-handle="agentHandle"
              :members="members"
              :topic-list="topicList"
              @titled="retitle(f.document!.id, $event)"
              @open-topic="(id: string, block?: string) => emit('open-topic', id, block)"
              @mention-click="emit('mention-click', $event)"
            />
            <ProjectFileTab
              v-else-if="f.project && mounted.has(fileKey(f.path))"
              v-show="shown(fileKey(f.path))"
              :class="enterClass(fileKey(f.path))"
              :project-id="projectId ?? topic?.project_id ?? null"
              :channel-id="topicId"
              :path="f.path"
              :lines="f.project.lines"
              @open-task="emit('open-card', $event)"
            />
            <PanelPreviewHost
              v-else-if="mounted.has(fileKey(f.path))"
              v-show="shown(fileKey(f.path))"
              :submit-question="submitQuestion"
              :class="enterClass(fileKey(f.path))"
              :topic-id="conversationId"
              :project-id="projectId"
              :path="f.path"
              :active="shown(fileKey(f.path))"
              :refresh-tick="refreshTick"
              :agent-name="agentName"
              :members="members"
              :agent-handle="agentHandle"
              @locate="emit('locate', $event)"
              @mention-click="emit('mention-click', $event)"
            />
          </template>
        </div>
      </ErrorBoundary>
    </template>
  </div>
</template>

<style scoped>
.work-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  background: var(--surface);
}
.tabpane-chat {
  display: flex;
  width: 100%;
  min-height: 0;
  height: 100%;
}
.tabpane-slot {
  display: flex;
  width: 100%;
  min-height: 0;
}
.tabpane-in {
  animation: tabpane-in var(--dur-base) var(--ease-standard);
}
.tabpane-in--right {
  --tabpane-from: 20px;
}
.tabpane-in--left {
  --tabpane-from: -20px;
}
@keyframes tabpane-in {
  from {
    opacity: 0;
    transform: translateX(var(--tabpane-from));
  }
}
@media (prefers-reduced-motion: reduce) {
  .tabpane-in {
    animation: none;
  }
}
.tabbody {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  /* Panel tab switches cross-fade this block only (lib/viewTransition.ts). */
  view-transition-name: wp-tabbody;
}
/* 每一格都占满这条 flex 行：按内容收缩的一格会让 Monaco 量到窄宽度后定死，面板右边空一条。 */
.tabbody > * {
  flex: 1 1 auto;
  min-width: 0;
}
/* 并排时右边那一格：排在后面，中间一道分隔线。 */
.tabbody > .tabpane--side {
  order: 1;
  border-left: 1px solid var(--line);
}
/* 挪进来的那 20px 不该撑出一条横向滚动。 */
.tabbody--phone {
  overflow: hidden;
}
</style>
