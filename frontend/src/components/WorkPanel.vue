<script setup lang="ts">
// 工作面板: the right-hand half of a topic — 平级 tabs, 总览 / 现场 / 改动 / 预览 /
// 定时与触发. It replaces the old 「文档 + 五个按需滑出的抽屉」 (预览/Git/现场/文件/资源):
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
// free zone when it is a room file the preview can draw, the 改动 tab otherwise.
//
// 页签分两段。固定区（总览 / 现场 / 改动 / 预览 / 定时与触发）不能关，位置记忆来自这里。自由区是
// 读者自己打开的那几份文件，可以关——变化是他自己做的，所以不算「页签自己出现和
// 消失」。单击打开的那一格是临时的，下一次打开会换掉它；双击就固定下来。不这样的
// 话，聊一小时能攒出二十个页签。
import type { OpenFileTab } from '../composables/useTopicMemory'
import type { AgentControlState, Block, PreviewInfo, ProjectMemberRow, Topic } from '../cx_types'
import type { DocReviewRequest } from '../lib/docReview'
import type { MemberActivityLine } from '../lib/memberActivity'
import type { PreviewLocate, SubmitPreviewQuestion } from '../lib/previewQuestion'
import type { CardPhase } from '../lib/topicState'
import type { TabDef, TabKey } from './panels/panelTabList'

import { computed, nextTick, ref, watch } from 'vue'

import { getPreview, getTopicWorkSummary, listRoomTasks, readPreviewFile } from '../api'
import { useTopicMemory } from '../composables/useTopicMemory'
import { previewCanShowInRoom } from '../lib/fileKind'
import { whenIdle } from '../lib/idle'

import ErrorBoundary from './common/ErrorBoundary.vue'
import PanelChanges from './panels/PanelChanges.vue'
import PanelOverview from './panels/PanelOverview.vue'
import PanelPreview from './panels/PanelPreview.vue'
import PanelSite from './panels/PanelSite.vue'
// 这一屏有哪几格（共用表 + 只有产品有的「定时与触发」）。这个文件里 `panelTabs` 已经
// 是「页签条要的那份数据」了，所以从 `workPanelTabs` 取。
import { workPanelTabs } from './panels/panelTabList'
import PanelTabs, { type PanelTab } from './panels/PanelTabs.vue'
import { confirmAnnotationDiscard } from './panels/preview/annotationDiscard'
import RoutinePanelHost from './routine/RoutinePanelHost.vue'

import { useCommands } from '@/commands'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
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
    // Project topics (A2): 文档 resolves live-ref badges and <#id> chips with it.
    topicList?: Topic[]
    // Which tab the URL asks for (`?tab=`). The address is the page's business,
    // so TopicView owns it and this component only reports its own moves — that
    // keeps the panel mountable without a router, which is how its four suites
    // exercise it. An unknown or absent value leaves the choice here.
    tab?: string
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
    // 地址里的 `?card=` —— 非空就是总览那一格正看着一张卡。
    openCardId?: string | null
    // 地址里的 `?block=`，而且开着一张卡：卡打开时停在它里面的这一条。
    cardFocusBlock?: string | null
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
  }>(),
  {
    submitQuestion: undefined,
    working: false,
    agentControl: null,
    siteTurns: () => ({}),
    topicList: () => [],
    openCardId: null,
    cardFocusBlock: null,
    memberNames: () => ({}),
    tab: undefined,
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
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string | null): void
  /** 卡片面板里的「去验收」——同 `chatEvents.review`，切到「改动」那一格。 */
  (e: 'review'): void
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
// 旧地址还带着 ?tab=doc / ?tab=tasks —— 两个 tab 都并进总览了，所以它们指的就是
// 总览。链接不该因为我们合并了界面而失效。
const TAB_ALIASES: Record<string, TabKey> = { doc: 'overview', tasks: 'overview' }
// ---- 自由区 ----
// 一份文件一个页签，键是 `file:<路径>`，地址里的 `?tab=` 用的也是它——「你看一下
// 这份报告」得是一条能发出去的链接。
const FILE_TAB = 'file:'
function fileKey(path: string): string {
  return FILE_TAB + path
}
const openFiles = ref<OpenFileTab[]>([])
// 自由区属于房间：切去别的房间再回来，开着的那几份还在。只记在这一次会话里。
const { filesByTopic } = useTopicMemory()

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
  if (!openFiles.value.some((f) => f.path === path)) placeFile(path)
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
  return e?.key === key ? `tabpane-in tabpane-in--${e.from}` : undefined
}

const overviewRef = ref<InstanceType<typeof PanelOverview> | null>(null)
const changesRef = ref<InstanceType<typeof PanelChanges> | null>(null)

const topicId = computed(() => props.topic?.id ?? null)
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
    void pollPreviewPointer()
    void pollWorkSummary()
    // 一轮里派出去的活，收工那一刻就该出现在 任务 那一格上。
    void pollThreads()
  }
)

// ---- 「有新内容」 on the 预览 tab. An artifact is deliberately NOT a chat
// message (it is a pointer, not something 芝士 said), and the tab is not the one
// you are on — so 芝士 could produce something worth looking at and the only way
// to find out was to click on a hunch. These two ids are the whole mechanism:
// what the server currently points at, and what this reader has already had on
// screen. ----
const previewLatest = ref<string | null>(null)
const previewSeen = ref<string | null>(null)
// 当前预览指着的那份文件。跑着的应用只有预览那一格画得出来（它是个进程，没有文件
// 可指），所以点开的是它就去那一格。
const previewPath = ref<string | null>(null)
const previewHasNew = computed(() => !!previewLatest.value && previewLatest.value !== previewSeen.value)

function markPreviewSeen(id?: string | null) {
  previewLatest.value = id ?? null
  previewSeen.value = id ?? null
}

// Fetch the POINTER only (no file read, no cookie priming) so the dot can appear
// while 预览 is not the open tab. Cheap enough to run on every turn boundary.
async function pollPreviewPointer(opts: { seen?: boolean } = {}) {
  const tid = props.topic?.id
  if (!tid) return
  let art: PreviewInfo | null = null
  try {
    art = await getPreview(tid)
  } catch {
    // A failed poll is not a state — leave the dot as it was. The real load
    // reports errors; this one only ever adds a hint.
    return
  }
  previewPath.value = art?.path ?? null
  const id = art?.artifact_id ?? null
  // Opening a topic must not greet the reader with a dot for something that was
  // already there before they arrived, and a poll while 预览 is open is looking
  // at it.
  if (opts.seen || active.value === 'preview') markPreviewSeen(id)
  else previewLatest.value = id
}

// ---- 这一格此刻有没有东西 ----
// 四格永远都在、位置不变：人记得住「改动在第三格」，一格时有时无的 tab 栏两次打开
// 可能不一样长。没东西的那一格字变浅，点进去是它自己的「暂无」。这里只回答有没有，
// 给字的深浅用，也给「开在哪一格」用——那一刻不该挑一格空的。
const summary = ref<{ changedFiles: string[]; hasRun: boolean }>({ changedFiles: [], hasRun: false })
// 这个房间的 summary 回来过没有。没回来之前「没有改动」还不是事实。
const summaryLoaded = ref(false)

async function pollWorkSummary(opts: { seen?: boolean } = {}) {
  const tid = props.topic?.id
  const pid = props.topic?.project_id
  if (!tid || !pid) return
  let next: { changed_files: string[]; has_run: boolean }
  try {
    next = await getTopicWorkSummary(pid, tid)
  } catch {
    // A failed poll is not a state: what the tabs show stays as it was. It still
    // counts as an answer for 「开在哪一格」, which then stays on the default tab
    // rather than waiting on a summary that may never come.
    summaryLoaded.value = true
    return
  }
  summary.value = { changedFiles: next.changed_files, hasRun: next.has_run }
  summaryLoaded.value = true
  // Arriving at a topic that already had changes is not news, exactly as it is
  // not news for the preview — the hint means 「这一轮干出来的」.
  if (opts.seen || active.value === 'changes') markChangesSeen()
}

// ---- 「改动变了」 on the 改动 tab, same shape as the preview's dot ----
// The count is the size of the review surface and is always worth showing; what
// needs a colour is 「这些改动是你上次看过之后才有的」. 芝士 works for minutes at a
// time and the reader is usually on 文档 while it does, so without this the only
// way to learn a turn produced anything was to click and compare.
const changesKey = computed(() => summary.value.changedFiles.join('\n'))
const changesSeen = ref<string>('')
const changesHasNew = computed(() => !!changesKey.value && changesKey.value !== changesSeen.value)
function markChangesSeen() {
  changesSeen.value = changesKey.value
}

// ---- 这个房间派出去了几件活 ----
// A signal, so 总览 can carry its count while closed and can stay out of the way
// of a room that never dispatched anything.
const threads = ref<{ total: number; open: number }>({ total: 0, open: 0 })

async function pollThreads() {
  const roomId = props.topic?.id
  if (!roomId) return
  try {
    // limit: 1 — see TaskProgress. Without it this asks for every card's whole
    // history just to count them.
    const rows = (await listRoomTasks(roomId, { limit: 1 })).data
    threads.value = { total: rows.length, open: rows.filter((r) => r.status === 'open').length }
  } catch {
    // A failed poll is not a state — same rule as the two polls above.
  }
}

function hasContent(key: TabKey): boolean {
  if (key === 'chat' || key === 'overview') return true
  // 「定时与触发」也是永远有得看的一格：没有规则时它写的是「还没有规则，点新建」——
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

const tabs = computed(() => workPanelTabs(props.withChat))
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
  if (tab.key === 'overview' && threads.value.total) {
    const { total, open } = threads.value
    const tasks = t('work.room.panel.taskCount', { count: total })
    return detailed(open ? pair(tasks, t('work.room.panel.inProgress', { count: open })) : tasks)
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
  if (key === 'overview' && threads.value.total) return { kind: 'count', count: threads.value.total }
  if (key === 'changes' && summary.value.changedFiles.length) {
    return { kind: 'count', count: summary.value.changedFiles.length, fresh: changesHasNew.value }
  }
  return undefined
}

/** 交给 `PanelTabs` 的那几格：文案、图标、有没有东西、信号。 */
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
  const id = props.topic?.id
  openFiles.value = (id && filesByTopic.get(id)) || []
  const asked = tabFromUrl()
  ensureFileFromUrl(asked)
  active.value = asked ?? defaultTab.value
  // 「URL 里显式带 ?tab= 时以 URL 为准」: an address that names a tab has already
  // decided, so the phase does not get to.
  settled.value = !!asked
  markPreviewSeen(null)
  if (id) {
    void pollPreviewPointer({ seen: true })
    // 这一条要等服务端算（几秒），而它只决定「改动」那一格的深浅、以及该开在哪一格。
    // 推到首屏画完、浏览器空下来再问：它不该和真正要把内容画出来的那些请求抢同一条
    // 网络和主线程。角标随后补上，逻辑不受影响（`summaryLoaded` 那只看的是有没有回过）。
    const openedId = id
    whenIdle(() => {
      if (props.topic?.id === openedId) void pollWorkSummary({ seen: true })
    })
    void pollThreads()
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
// 这三样都是「把总览里某样东西掀到眼前」（聊天里点了「查看改动 / 看这一轮」），不是
// 用户主动离开正在标注的那张图：绕过守卫切过去，切换与随后的那一下都真的发生。
async function pulse() {
  await setTab('overview', { guard: false })
  await nextTick()
  overviewRef.value?.pulse()
}
async function highlightTurn(turnId: string) {
  await setTab('overview', { guard: false })
  await nextTick()
  overviewRef.value?.highlightTurn(turnId)
}
async function reviewDoc(request: DocReviewRequest) {
  await setTab('overview', { guard: false })
  await nextTick()
  overviewRef.value?.reviewEdits(request)
}
// A chip is a path with no store, and a room has three: its own files (what 芝士
// delivered and what people uploaded — no branch, no history), a task's worktree,
// and the project's current code. So find the file FIRST and pick the tab from
// where it turned out to be. Done the other way round, a reader who clicks a file
// 芝士 just made gets the 改动 tab appearing out of nowhere, a listing that does
// not contain it, and then a read error on top — the file was never in a tree.
async function openFile(path: string, taskId?: string | null) {
  // A chip may carry the lines it was pointing at (`src/a.ts:12-30`) — that part
  // names a place inside the file, not a file, and neither store knows it.
  const want = path.replace(/:\d+(?:-\d+)?$/, '')
  // 「房间文件」这一档包含网页：内容域按路径服务房间里的任意一份，所以一份 html
  // 在这里画得出来。仓库树里的 .html 不在此列（那不是房间文件），仍然去「改动」
  // 那格看 diff。
  if (previewCanShowInRoom(want) && (await inRoomFiles(want))) {
    openFileTab(want)
    return
  }
  // 剩下的画不出来的（跑着的应用）只剩预览那一格。它指着谁要现问：芝士一轮里摆出来
  // 的东西，这里手上那份记录要等这一轮结束才更新。
  await pollPreviewPointer()
  if (want === previewPath.value) {
    await setTab('preview')
    return
  }
  // 这一步是用户点了一份文件，该走守卫问一句。问不到「可以走」就当场收手：`setTab`
  // 停在原地，`changesRef` 要么是空的、要么指向一份没露面的「改动」——再往下走就是
  // 一次没有落地、也没人知道的假动作。
  if (!(await setTab('changes'))) return
  await nextTick()
  // `undefined`, not `null`: a message under no card says nothing about which
  // source holds the file, while `null` means 「项目当前代码」 — and a file this
  // room is still working on is not on main yet.
  await changesRef.value?.openFile(want, taskId ?? undefined)
}

/** 这份文件是不是房间自己的（芝士交付的、人传上来的）。不是就去树上找。 */
async function inRoomFiles(path: string): Promise<boolean> {
  const tid = props.topic?.id
  if (!tid) return false
  try {
    await readPreviewFile(tid, path)
    return true
  } catch {
    return false
  }
}

// 放进自由区，不切过去：开着的就不动，否则占临时位——有一格临时的就在原位换掉它，
// 没有就排到最后。
function placeFile(path: string) {
  if (openFiles.value.some((f) => f.path === path)) return
  const next = [...openFiles.value]
  const temp = next.findIndex((f) => !f.pinned)
  if (temp >= 0) next.splice(temp, 1, { path, pinned: false })
  else next.push({ path, pinned: false })
  setFiles(next)
}

function openFileTab(path: string) {
  placeFile(path)
  setTab(fileKey(path))
}

function pinFile(path: string) {
  setFiles(openFiles.value.map((f) => (f.path === path ? { ...f, pinned: true } : f)))
}

// 关掉的是正看着的那一格，就落到它旁边那一格；自由区空了就回总览。
//
// 关掉正看着的那一格＝离开一块正在标注的图，所以先问一句，问完再动手拆。后台那几格
// （没在看）不会拦：它们本就登记不上（见 `annotationDiscard`）。
async function closeFile(path: string) {
  const at = openFiles.value.findIndex((f) => f.path === path)
  if (at < 0) return
  const key = fileKey(path)
  const wasActive = active.value === key
  if (wasActive && !(await confirmAnnotationDiscard())) return
  const next = openFiles.value.filter((f) => f.path !== path)
  setFiles(next)
  const nextMounted = new Set(mounted.value)
  nextMounted.delete(key)
  mounted.value = nextMounted
  if (!wasActive) return
  const neighbour = next[Math.min(at, next.length - 1)]
  setTab(neighbour ? fileKey(neighbour.path) : 'overview')
}

function setFiles(next: OpenFileTab[]) {
  openFiles.value = next
  const tid = props.topic?.id
  if (tid) filesByTopic.set(tid, next)
}

// 对话栏的 socket 上来了现场的一行：交给现场那格。那格还没打开过就不用管，它第一次
// 打开时会整段读一遍。
const siteRef = ref<InstanceType<typeof PanelSite> | null>(null)
function siteBlock(block: Block) {
  siteRef.value?.receive(block)
}

// 面板此刻在画哪一格。地址不一定写得出来——平板横放里自动挑中的那一格就没写进地址，
// 而收起浮层再打开要回到它，所以这里是那份记忆的出处（TopicView 打开浮层时来问）。
defineExpose({ pulse, highlightTurn, reviewDoc, openFile, siteBlock, activeTab: () => active.value })
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
        @select="setTab"
        @close-file="closeFile"
        @pin-file="pinFile"
      />

      <!-- Panel content (not the tab strip) gets its own boundary: a tab that
           throws shows the fallback here while the strip stays usable.
           resetKey = topic id, so switching topics recovers on its own. -->
      <ErrorBoundary variant="compact" :reset-key="topic.id">
        <div class="tabbody" :class="{ 'tabbody--phone': withChat }">
          <!-- 对话这一格由 TopicView 填（它拿着 ChatPanel 的那一堆接线）。一直挂着
               而不是切走就卸载：卸掉会断掉连接、丢掉滚动位置。 -->
          <div v-if="withChat" v-show="active === 'chat'" class="tabpane-chat" :class="enterClass('chat')">
            <slot name="chat" />
          </div>
          <PanelOverview
            v-show="active === 'overview'"
            ref="overviewRef"
            :class="enterClass('overview')"
            :agent-name="agentName"
            :agent-handle="agentHandle"
            :members="members"
            :topic="topic"
            :activity-tick="activityTick"
            :topic-list="topicList"
            :active="active === 'overview'"
            :refresh-tick="refreshTick"
            :open-card-id="openCardId"
            :card-focus-block="cardFocusBlock"
            :member-names="memberNames"
            @open-topic="emit('open-topic', $event)"
            @open-card="emit('open-card', $event)"
            @review="emit('review')"
            @mention-click="emit('mention-click', $event)"
            @open-file="openFile"
            @open-output="openFileTab"
          />
          <PanelSite
            v-if="mounted.has('site')"
            v-show="active === 'site'"
            ref="siteRef"
            :class="enterClass('site')"
            :agent-name="agentName"
            :topic="topic"
            :active="active === 'site'"
            :running-turns="siteTurns"
            :refresh-tick="refreshTick"
            :member-names="memberNames"
            :working="working"
            :activity="activity"
            :agent-control="agentControl"
            @open-file="openFile"
            @open-topic="emit('open-topic', $event)"
            @mention-click="emit('mention-click', $event)"
          />
          <PanelChanges
            v-if="mounted.has('changes')"
            v-show="active === 'changes'"
            ref="changesRef"
            :class="enterClass('changes')"
            :topic-id="topicId"
            :task-id="openCardId"
            :read-only="topic?.status === 'archived'"
            :project-id="projectId"
            :active="active === 'changes'"
            :refresh-tick="refreshTick"
          />
          <PanelPreview
            v-if="mounted.has('preview')"
            v-show="active === 'preview'"
            :submit-question="submitQuestion"
            :class="enterClass('preview')"
            :topic-id="topicId"
            :project-id="projectId"
            :active="active === 'preview'"
            :refresh-tick="refreshTick"
            @loaded="markPreviewSeen"
            @locate="emit('locate', $event)"
            @open-file="openFileTab"
          />
          <!-- 这个房间的规则：到点或发生某件事时它自己开工。取数在新的一轮结束时跟一次
             （`refreshTick`）—— 芝士可能刚在房间里起草了一条。 -->
          <RoutinePanelHost
            v-if="mounted.has('routines')"
            v-show="active === 'routines'"
            :class="enterClass('routines')"
            :topic-id="topicId"
            :project-id="projectId"
            :refresh-tick="refreshTick"
          />
          <template v-for="f in openFiles" :key="fileKey(f.path)">
            <PanelPreview
              v-if="mounted.has(fileKey(f.path))"
              v-show="active === fileKey(f.path)"
              :submit-question="submitQuestion"
              :class="enterClass(fileKey(f.path))"
              :topic-id="topicId"
              :project-id="projectId"
              :path="f.path"
              :active="active === fileKey(f.path)"
              :refresh-tick="refreshTick"
              @locate="emit('locate', $event)"
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
  /* tabbody 是一条 flex 行，这一格必须占满它——按内容收缩的话，输入框只有半屏宽。 */
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  min-height: 0;
  height: 100%;
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
}
/* 挪进来的那 20px 不该撑出一条横向滚动。 */
.tabbody--phone {
  overflow: hidden;
}
</style>
