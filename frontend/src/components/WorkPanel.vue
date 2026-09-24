<script setup lang="ts">
// 工作面板: the right-hand half of a topic — 平级 tabs, 总览 / 现场 / 改动 / 预览. It replaces the old 「文档 + 五个按需滑出的抽屉」 (预览/Git/现场/文件/资源):
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
// 页签分两段。固定区（总览 / 现场 / 改动 / 预览）不能关，位置记忆来自这里。自由区是
// 读者自己打开的那几份文件，可以关——变化是他自己做的，所以不算「页签自己出现和
// 消失」。单击打开的那一格是临时的，下一次打开会换掉它；双击就固定下来。不这样的
// 话，聊一小时能攒出二十个页签。
import type { PreviewInfo, Topic } from '../cx_types'
import type { TopicPhase } from '../lib/topicState'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { getPreview, getTopicWorkSummary, listRoomTasks, readPreviewFile } from '../api'
import { fileIcon, previewCanShowInRoom } from '../lib/fileKind'

import PanelChanges from './panels/PanelChanges.vue'
import PanelOverview from './panels/PanelOverview.vue'
import PanelPreview from './panels/PanelPreview.vue'
import PanelSite from './panels/PanelSite.vue'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // Bumped by the parent on AI activity (turn-done / a platform resource the
    // turn changed) so 文档 reloads the doc 芝士 just wrote. See TopicView
    // activityTick.
    activityTick: number
    // 芝士 正在这个话题里干活 —— tab 栏据此给「现场」加一个跳动的点。
    working?: boolean
    // Project topics (A2): 文档 resolves live-ref badges and <#id> chips with it.
    topicList?: Topic[]
    // Which tab the URL asks for (`?tab=`). The address is the page's business,
    // so TopicView owns it and this component only reports its own moves — that
    // keeps the panel mountable without a router, which is how its four suites
    // exercise it. An unknown or absent value leaves the choice here.
    tab?: string
    // 话题此刻处在哪一段. Only used to pick which tab a topic OPENS on, and only
    // when the address named none — after that it is the reader's choice.
    phase?: TopicPhase
    // 手机上对话不是左边那一栏，是这条 tab 栏的第一格——一屏放不下两栏，而这两
    // 样东西本来就是平级的。开着它的时候 `chat` 插槽就是这一格的内容。
    withChat?: boolean
    // 地址里的 `?card=` —— 非空就是总览那一格正看着一张卡。
    openCardId?: string | null
    // 房间名册 handle → 名字。现场那一格用它给每一行署名。一路透传：漏掉它不
    // 报错，只是那一格里写的是 handle。
    memberNames?: Record<string, string>
  }>(),
  {
    working: false,
    topicList: () => [],
    openCardId: null,
    memberNames: () => ({}),
    tab: undefined,
    phase: undefined,
    withChat: false,
  }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string | null): void
  /** 卡片面板里的「去验收」——同 `chatEvents.review`，切到「改动」那一格。 */
  (e: 'review'): void
  (e: 'mention-click', handle: string): void
  (e: 'update:tab', key: string): void
  // 预览面板里读者指着文档说的那一句，交给拿着对话的那一层。
  (e: 'locate', message: string): void
}>()

type TabKey = 'chat' | 'overview' | 'site' | 'changes' | 'preview'
interface TabDef {
  key: TabKey
  label: string
  icon: string
}
const ALL_TABS: TabDef[] = [
  { key: 'chat', label: '对话', icon: 'mdi-message-outline' },
  // 文档 和 任务 合成了一格。它们回答的是同一个问题的两半——「这个房间在干什么」
  // ——分成两格意味着看完一半得先想起来还有另一半，于是大多数人只看文档，房间里
  // 有几条活在跑就没人知道。
  { key: 'overview', label: '总览', icon: 'mdi-file-document-outline' },
  { key: 'site', label: '现场', icon: 'mdi-hammer-wrench' },
  { key: 'changes', label: '改动', icon: 'mdi-source-branch' },
  { key: 'preview', label: '预览', icon: 'mdi-eye-outline' },
]
// 地址没指定、阶段也没话说的时候落在哪一格：手机上是对话（你进话题多半是来说话
// 的），桌面上对话就在旁边那一栏，所以是总览。
const defaultTab = computed<TabKey>(() => (props.withChat ? 'chat' : 'overview'))
// 旧地址还带着 ?tab=doc / ?tab=tasks —— 两个 tab 都并进总览了，所以它们指的就是
// 总览。链接不该因为我们合并了界面而失效。
const TAB_ALIASES: Record<string, TabKey> = { doc: 'overview', tasks: 'overview' }
// ---- 自由区 ----
// 一份文件一个页签，键是 `file:<路径>`，地址里的 `?tab=` 用的也是它——「你看一下
// 这份报告」得是一条能发出去的链接。
interface FileTab {
  path: string
  pinned: boolean
}
const FILE_TAB = 'file:'
function fileKey(path: string): string {
  return FILE_TAB + path
}
function fileName(path: string): string {
  return path.split('/').pop() || path
}
const openFiles = ref<FileTab[]>([])
// 自由区属于房间：切去别的房间再回来，开着的那几份还在。只记在这一次会话里。
const filesByTopic = new Map<string, FileTab[]>()

const active = ref<string>(defaultTab.value)
/** The URL's answer, if it names a tab that exists (or one that used to). */
function tabFromUrl(): string | null {
  const asked = props.tab
  if (!asked) return null
  if (asked.startsWith(FILE_TAB) && asked.length > FILE_TAB.length) return asked
  if (ALL_TABS.some((t) => t.key === asked)) return asked as TabKey
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
// 没发生。选中态一变就把它带回视野里。
const tabbarRef = ref<HTMLElement | null>(null)
watch(active, () => {
  void nextTick(() => {
    const on = tabbarRef.value?.querySelector('[aria-selected="true"]')
    on?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  })
})

// 选中那一格下面的线是一条，换页签时从旧的那一格滑到新的那一格（§9.2：位置变了，
// 就让人看见它是从哪儿挪过来的）。每一格各画一条的话，换页签是一条消失、另一条
// 凭空出现，读不出「从这儿到那儿」。
//
// 量的是选中那一格自己的盒子，所以一格的宽度变了（计数出现、字体加载完）也得重量
// 一次——盯着的就是那一格。第一次落位不演：打开房间时线本来就在那儿。
const ink = ref<{ left: number; width: number } | null>(null)
const inkMoves = ref(false)
let inkWatch: ResizeObserver | null = null
let inkTarget: Element | null = null
function placeInk() {
  const on = tabbarRef.value?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
  if (!on) {
    ink.value = null
    return
  }
  // 页签在 `.tabbar__file` 里的时候 offsetLeft 量的也是到 `.tabbar` 的距离：那层
  // 包装没有定位，偏移的基准一路落到定了位的 `.tabbar` 上。
  ink.value = { left: on.offsetLeft, width: on.offsetWidth }
  // 只在选中的换了一格时改盯的对象：`observe` 一挂上就先回调一次，回调里再
  // `disconnect` + `observe` 同一格，就是每一帧都在重挂、每一帧都在报 ResizeObserver
  // 循环。
  if (on !== inkTarget && typeof ResizeObserver !== 'undefined') {
    inkWatch?.disconnect()
    inkWatch ??= new ResizeObserver(() => placeInk())
    inkWatch.observe(on)
    inkTarget = on
  }
  if (!inkMoves.value) requestAnimationFrame(() => (inkMoves.value = true))
}
watch([active, () => openFiles.value.length, tabbarRef], () => void nextTick(placeInk), { immediate: true })
onBeforeUnmount(() => inkWatch?.disconnect())
const inkStyle = computed(() =>
  ink.value
    ? { transform: `translateX(${ink.value.left + 8}px)`, width: `${Math.max(0, ink.value.width - 16)}px` }
    : { display: 'none' }
)

// Every move the panel makes goes through here, so the address always says what
// is on screen — 「你来看一眼这个 diff」的链接成立的前提就是这个。
function setTab(key: string) {
  settled.value = true
  active.value = key
  if (key === 'changes') markChangesSeen()
  emit('update:tab', key)
}

// ---- 开在哪个 tab 上 (规则 3) ----
// Opening a topic is the one moment choosing a tab is not snatching the view,
// so it is the one moment the panel gets to choose: 芝士 干着活的时候你多半是来
// 看它在干什么的，卡等你验收的时候你是来看它干了什么的。
//
// `settled` is what keeps it to that moment. The phase arrives asynchronously —
// the accept card has to load before anyone knows a card is pending, and until
// it has the prop is undefined rather than 「没有卡」 — so this cannot run on the
// topic switch itself. It runs on the first phase this topic reports, and never
// again unless another topic is opened.
const settled = ref(false)

// 只挑有东西可看的那一格：挑中一格空的，人一进房间看到的就是一句「暂无」——
// 待验收的房间没有改动文件（比如项目还没接仓库）时，原来就落在一块报错上。
function tabForPhase(phase: TopicPhase): TabKey {
  if (phase === 'working') return 'site'
  if ((phase === 'reviewing' || phase === 'delivering') && hasContent('changes')) return 'changes'
  return defaultTab.value
}

// Back / forward, or someone pasting a link into the open topic.
watch(
  () => props.tab,
  () => {
    const asked = tabFromUrl()
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
  if (props.topic?.id !== tid) return
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
    if (props.topic?.id === tid) summaryLoaded.value = true
    return
  }
  if (props.topic?.id !== tid) return
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
    if (props.topic?.id !== roomId) return
    threads.value = { total: rows.length, open: rows.filter((r) => r.status === 'open').length }
  } catch {
    // A failed poll is not a state — same rule as the two polls above.
  }
}

function hasContent(key: TabKey): boolean {
  if (key === 'chat' || key === 'overview') return true
  // 改动属于**树**：一棵树 = 一个分支 = 一个 PR = 一批活，所以这份 diff 是这个
  // 房间当前这一批一起写出来的。
  if (key === 'changes') return summary.value.changedFiles.length > 0
  // 现场 is where 芝士 works: it has something once the room has run, and from the
  // first moment of the first turn (before the session id is captured).
  if (key === 'site') return summary.value.hasRun || props.working
  return !!previewLatest.value
}

const tabs = computed(() => ALL_TABS.filter((t) => t.key !== 'chat' || props.withChat))

/** What the signal on a tab means, for people who reach it by hover or reader. */
function tabTitle(t: TabDef): string {
  if (t.key === 'site' && props.working) return `${t.label}（芝士正在工作）`
  if (t.key === 'overview' && threads.value.total) {
    const { total, open } = threads.value
    return open ? `${t.label}（${total} 件任务，${open} 件进行中）` : `${t.label}（${total} 件任务）`
  }
  if (t.key === 'preview' && previewHasNew.value) return `${t.label}（有新内容）`
  if (t.key === 'changes' && summary.value.changedFiles.length) {
    const n = summary.value.changedFiles.length
    return changesHasNew.value ? `${t.label}（${n} 个文件，有新改动）` : `${t.label}（${n} 个文件）`
  }
  return t.label
}

// Topic switch: the address decides, 文档 when it says nothing. Baseline the dot
// against whatever this topic already had, so opening a topic — including
// straight onto 预览 from someone's link — never greets you with a hint for work
// that was there before you arrived.
watch(
  () => props.topic?.id,
  (id) => {
    openFiles.value = (id && filesByTopic.get(id)) || []
    const asked = tabFromUrl()
    ensureFileFromUrl(asked)
    active.value = asked ?? defaultTab.value
    // 「URL 里显式带 ?tab= 时以 URL 为准」: an address that names a tab has already
    // decided, so the phase does not get to.
    settled.value = !!asked
    markPreviewSeen(null)
    previewPath.value = null
    summary.value = { changedFiles: [], hasRun: false }
    summaryLoaded.value = false
    changesSeen.value = ''
    threads.value = { total: 0, open: 0 }
    if (id) {
      void pollPreviewPointer({ seen: true })
      void pollWorkSummary({ seen: true })
      void pollThreads()
    }
  },
  { immediate: true }
)

// Declared after the topic watcher on purpose: both fire immediately on mount,
// in declaration order, and this one must see the `settled` that watcher sets.
//
// 待验收 / 交付中要等 summary 回来才挑：开在「改动」的前提是真有改动，而两个请求
// 同时发出，谁先到说不准。等到的是一个事实，不是一场赛跑。
watch(
  [() => props.phase, summaryLoaded],
  ([phase, loaded]) => {
    if (settled.value || !phase) return
    if ((phase === 'reviewing' || phase === 'delivering') && !loaded) return
    const want = tabForPhase(phase)
    if (want === active.value) settled.value = true
    else setTab(want)
  },
  { immediate: true }
)

// ---- The panel's outward API (TopicView holds a ref) ----
function pulse() {
  setTab('overview')
  void nextTick(() => overviewRef.value?.pulse())
}
function highlightTurn(turnId: string) {
  setTab('overview')
  void nextTick(() => overviewRef.value?.highlightTurn(turnId))
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
    setTab('preview')
    return
  }
  setTab('changes')
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
function closeFile(path: string) {
  const at = openFiles.value.findIndex((f) => f.path === path)
  if (at < 0) return
  const next = openFiles.value.filter((f) => f.path !== path)
  setFiles(next)
  const key = fileKey(path)
  const nextMounted = new Set(mounted.value)
  nextMounted.delete(key)
  mounted.value = nextMounted
  if (active.value !== key) return
  const neighbour = next[Math.min(at, next.length - 1)]
  setTab(neighbour ? fileKey(neighbour.path) : 'overview')
}

function setFiles(next: FileTab[]) {
  openFiles.value = next
  const tid = props.topic?.id
  if (tid) filesByTopic.set(tid, next)
}

defineExpose({ pulse, highlightTurn, openFile })
</script>

<template>
  <div class="work-panel">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>选择一个话题查看文档</div>
      </div>
    </div>

    <template v-else>
      <div ref="tabbarRef" class="tabbar" role="tablist">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          role="tab"
          class="tabbar__tab"
          :class="{ 'tabbar__tab--on': active === t.key, 'tabbar__tab--empty': !hasContent(t.key) }"
          :aria-selected="active === t.key"
          :title="tabTitle(t)"
          @click="setTab(t.key)"
        >
          <v-icon size="16">{{ t.icon }}</v-icon>
          {{ t.label }}
          <!-- 信号上 Tab，不抢占视图: 芝士 works for minutes at a time and the
               reader is usually somewhere else while it does, so what it
               produced has to be visible from the tab it produced it on. None
               of these ever selects a tab for you. -->
          <span v-if="t.key === 'site' && working" class="tabbar__pulse" />
          <!-- A dot, not a count: there is only ever one current preview, so a
               number would be noise. -->
          <span v-if="t.key === 'preview' && previewHasNew" class="tabbar__dot" />
          <!-- 有几件活在跑。和 改动 一样用数字而不是点：几件在跑本身就是要看的
               那个信息。它不变色——派出去的活不是「你还没看过的东西」。 -->
          <span v-if="t.key === 'overview' && threads.total" class="tabbar__count">{{ threads.total }}</span>
          <!-- 改动 is the opposite: how much there is to review is the useful
               part, so the count carries the signal and turns amber when it is
               work you have not looked at yet. -->
          <span
            v-if="t.key === 'changes' && summary.changedFiles.length"
            class="tabbar__count"
            :class="{ 'tabbar__count--new': changesHasNew }"
            >{{ summary.changedFiles.length }}</span
          >
        </button>
        <!-- 自由区。关闭钮和页签是兄弟，不是它的孩子：按钮里套按钮不合法，读屏也会
             把两者念成一个东西。 -->
        <span v-if="openFiles.length" class="tabbar__sep" aria-hidden="true" />
        <div
          v-for="f in openFiles"
          :key="fileKey(f.path)"
          class="tabbar__file"
          :class="{ 'tabbar__file--temp': !f.pinned }"
        >
          <button
            type="button"
            role="tab"
            class="tabbar__tab"
            :class="{ 'tabbar__tab--on': active === fileKey(f.path) }"
            :aria-selected="active === fileKey(f.path)"
            :title="f.pinned ? f.path : `${f.path}（双击固定这个页签）`"
            @click="setTab(fileKey(f.path))"
            @dblclick="pinFile(f.path)"
          >
            <v-icon size="16">{{ fileIcon(f.path) }}</v-icon>
            <span class="tabbar__name">{{ fileName(f.path) }}</span>
          </button>
          <button
            type="button"
            class="tabbar__close"
            :aria-label="`关闭 ${fileName(f.path)}`"
            :title="`关闭 ${fileName(f.path)}`"
            @click="closeFile(f.path)"
          >
            <v-icon size="14">mdi-close</v-icon>
          </button>
        </div>
        <span class="tabbar__ink" :class="{ 'tabbar__ink--moves': inkMoves }" :style="inkStyle" aria-hidden="true" />
      </div>

      <div class="tabbody">
        <!-- 对话这一格由 TopicView 填（它拿着 ChatPanel 的那一堆接线）。一直挂着
             而不是切走就卸载：卸掉会断掉连接、丢掉滚动位置。 -->
        <div v-if="withChat" v-show="active === 'chat'" class="tabpane-chat">
          <slot name="chat" />
        </div>
        <PanelOverview
          v-show="active === 'overview'"
          ref="overviewRef"
          :topic="topic"
          :activity-tick="activityTick"
          :topic-list="topicList"
          :active="active === 'overview'"
          :refresh-tick="refreshTick"
          :open-card-id="openCardId"
          @open-topic="emit('open-topic', $event)"
          @open-card="emit('open-card', $event)"
          @review="emit('review')"
          @mention-click="emit('mention-click', $event)"
          @open-file="openFile"
        />
        <PanelSite
          v-if="mounted.has('site')"
          v-show="active === 'site'"
          :topic="topic"
          :active="active === 'site'"
          :member-names="memberNames"
          :working="working"
        />
        <PanelChanges
          v-if="mounted.has('changes')"
          v-show="active === 'changes'"
          ref="changesRef"
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
          :topic-id="topicId"
          :project-id="projectId"
          :active="active === 'preview'"
          :refresh-tick="refreshTick"
          @loaded="markPreviewSeen"
          @locate="emit('locate', $event)"
          @open-file="openFileTab"
        />
        <template v-for="f in openFiles" :key="fileKey(f.path)">
          <PanelPreview
            v-if="mounted.has(fileKey(f.path))"
            v-show="active === fileKey(f.path)"
            :topic-id="topicId"
            :project-id="projectId"
            :path="f.path"
            :active="active === fileKey(f.path)"
            :refresh-tick="refreshTick"
            @locate="emit('locate', $event)"
          />
        </template>
      </div>
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
.tabbar {
  position: relative;
  display: flex;
  flex: 0 0 auto;
  align-items: stretch;
  gap: 2px;
  padding: 0 6px;
  border-bottom: 1px solid var(--line);
  /* 一屏放不下的时候横着滚，而不是把每一格压扁：挤压是没有边界的——tab 只会越
     加越多，而窄屏上第一个被挤没的永远是文字，剩下一排认不出来的图标。滚动条不
     画出来，因为这条栏本来就只有一行高，一条滚动条会占掉它三分之一。 */
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}
.tabbar::-webkit-scrollbar {
  display: none;
}
.tabbar__tab {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
}
.tabbar__tab:hover {
  color: var(--ink);
}
/* 这一格此刻没东西：字退到 --faint，但照样能点，点进去是它自己的「暂无」。 */
.tabbar__tab--empty:not(.tabbar__tab--on) {
  color: var(--faint);
}
/* 固定区和自由区之间的那一道：前面几格永远在，后面几格是你自己开的。 */
.tabbar__sep {
  flex: 0 0 auto;
  align-self: center;
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: var(--line);
}
.tabbar__file {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
}
.tabbar__file .tabbar__tab {
  padding-right: 4px;
}
.tabbar__name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 临时位：下一次打开会换掉它。斜体是编辑器里通行的说法；双击就不斜了。 */
.tabbar__file--temp .tabbar__name {
  font-style: italic;
}
.tabbar__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-right: 4px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--faint);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.tabbar__close:hover {
  background: var(--fill);
  color: var(--ink);
}
/* 选中态: ink + 一条下边线。琥珀只留给唯一主操作、导航选中态和品牌标，工作面板的
   tab 不是导航，所以用中性墨色。 */
.tabbar__tab--on {
  color: var(--ink);
  font-weight: 600;
}
.tabbar__ink {
  position: absolute;
  bottom: -1px;
  left: 0;
  height: 2px;
  background: var(--ink);
  pointer-events: none;
}
.tabbar__ink--moves {
  transition:
    transform var(--dur-base) var(--ease-standard),
    width var(--dur-base) var(--ease-standard);
}
/* 有新内容 —— 琥珀在这条 tab 栏里只给「有东西等你看」，不给选中态。 */
.tabbar__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}
/* 芝士正在这个 tab 后面干活。呼吸而不是常亮：常亮说的是「有个东西」，呼吸说的
   是「正在发生」，而现场这一片的全部意义就是后者。 */
.tabbar__pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
  animation: tabbar-breathe 1.6s ease-in-out infinite;
}
@keyframes tabbar-breathe {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
@media (prefers-reduced-motion: reduce) {
  .tabbar__pulse {
    animation: none;
  }
}
/* 改动的规模。默认是中性的事实，只有「你还没看过的那些」才配琥珀。 */
.tabbar__count {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--faint);
}
.tabbar__count--new {
  color: var(--accent);
}
.tabbody {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
</style>
