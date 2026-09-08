<script setup lang="ts">
import type { Project, ProjectAgent, Topic } from '../cx_types'
import type { FlatRow, VisibleRow } from '../lib/topicTree'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { listProjectAgents } from '../api'
import { columnDotStyle } from '../lib/board'
import { cancelPrefetch, prefetchOnHover } from '../lib/routePrefetch'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '../lib/topicTitle'
import {
  ancestorPathIds,
  isMyTopic,
  loadExpandedTopics,
  loadOthersGroupOpen,
  partitionByRelevance,
  saveExpandedTopics,
  saveOthersGroupOpen,
  visibleRows,
} from '../lib/topicTree'
import { avatarColor, avatarInitial } from '../utils/avatar'

import LoadingSkeleton from './common/LoadingSkeleton.vue'
import SecondaryNavigation from './common/Navigation/SecondaryNavigation.vue'

const props = defineProps<{
  projects: Project[]
  selectedProjectId: string | null
  topics: Topic[]
  selectedTopicId: string | null
  loadingTopics: boolean
  // Which 项目文档 is open in the main area ('charter'|'decisions'|'weeklies'|
  // 'memory'), or null when none — the rail shows ONE 项目文档 row, active for
  // any of them, because which document is open is the page's business now.
  activeDocs?: string | null
  // Drawer width (px), made resizable by the parent.
  width?: number
  // 话题级未读 (Feishu-style): {topicId: count}; missing key = no unread.
  unreadMap?: Record<string, number>
  // 私聊未读: {peerHandle: count}, `cheese` = 和芝士那一间。侧栏只用它的**总数**，
  // 挂在「成员」那一行上；是谁找你在成员页里说（每个人的私聊按钮上各带各的）。
  // 和 unreadMap 分开是因为私聊是按对方 handle 编址的，没有话题 id。
  privateUnreadMap?: Record<string, number>
  // 整页形态: 手机上话题列表是页面栈的一层，占满内容区，不是侧边抽屉。
  page?: boolean
}>()

const emit = defineEmits<{
  (e: 'select-topic', id: string): void
  // 指针停在一行上：让父组件（拥有这一行的路由的那个）顺手把它预热了。点这一行
  // 会发生什么由 select-topic 的接收方决定，所以「提前准备什么」也归它。
  (e: 'hover-topic', id: string): void
  (e: 'leave-topic'): void
  (e: 'create-topic', title: string, agentInstanceId?: string | null): void
  // 归档去向: manual archive / unarchive from the row's ⋯ actions.
  (e: 'archive-topic', id: string): void
  (e: 'unarchive-topic', id: string): void
  // Rename a topic's title from the row's ⋯ actions.
  (e: 'rename-topic', payload: { id: string; title: string }): void
  // Open 项目文档 in the main area. The rail always asks for 章程 — the page
  // itself carries the tabs that reach the other three.
  (e: 'select-docs', kind: 'charter' | 'decisions' | 'weeklies' | 'memory'): void
  // Live drawer width while dragging the right edge.
  (e: 'update:width', w: number): void
}>()

// Drag the rail's right edge — emit the cursor's x (= rail width from the left).
function startResize(e: MouseEvent) {
  e.preventDefault()
  const move = (ev: MouseEvent) => emit('update:width', ev.clientX)
  const stop = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', stop)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', stop)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

// 项目级页面（总览/日历）住在话题列表最上面的置顶行里，和话题行同一种视觉
// 语法——它们和这个侧栏里的其他一切一样，只换内容区。项目设置不在这里：它是
// 一年点两次的东西，收进项目头的 ⋯ 菜单。
const router = useRouter()
const route = useRoute()

const projectPages = [
  { key: 'overview', label: '总览', icon: 'mdi-view-agenda-outline' },
  { key: 'workspace-running', label: '看板', icon: 'mdi-view-column-outline' },
  { key: 'calendar', label: '日历', icon: 'mdi-calendar-outline' },
  { key: 'project-agents', label: 'AI 队友', icon: 'mdi-robot-outline' },
  // 成员紧挨着 AI 队友：这两行答的是同一个问题的两半——这个项目里都有谁。
  { key: 'project-members', label: '成员', icon: 'mdi-account-group-outline' },
] as const
function openProjectPage(name: string) {
  if (!props.selectedProjectId) return
  router.push({ name, params: { projectId: props.selectedProjectId } })
}
// 谁负责 push，谁负责预热：指针停住的时候把这个页面的代码先下下来，等真按下去时
// 只剩下拉数据那一段。
function hoverProjectPage(name: string) {
  if (!props.selectedProjectId) return
  prefetchOnHover({ router, to: { name, params: { projectId: props.selectedProjectId } } })
}

// 换项目落在项目地址本身，而不是它的某个话题：哪个话题该开着是那个项目的事
// （手机上这个地址就是它的话题列表，桌面上它自己跳大本营）。
function openProject(projectId: string) {
  if (projectId === props.selectedProjectId) return
  router.push({ name: 'workspace-project', params: { projectId } })
}

// New topic: don't ask the human for a title — create an untitled one and open
// it; the title is derived from the first message (and 芝士 can refine it).
//
// 队友是另一回事，必须在这一刻选：换队友会丢掉话题的会话，所以事后再改改的是一
// 段已经有人说过话的对话。菜单第一项就是默认那个，常用路径仍然是「点开、点第一
// 项」两下，而且点之前就看得见这个房间要交给谁。
function newTopic(agentInstanceId?: string | null) {
  emit('create-topic', '', agentInstanceId)
}

// 这个项目有哪些队友，供上面那个菜单用。拿不到就退化成不带队友创建（跟项目默认
// 走）—— 一个还没上线 agent 接口的环境不该连新建话题都点不动。
const projectAgents = ref<ProjectAgent[]>([])
async function loadProjectAgents(pid: string | null | undefined) {
  if (!pid) {
    projectAgents.value = []
    return
  }
  try {
    projectAgents.value = (await listProjectAgents(pid)).data
  } catch {
    projectAgents.value = []
  }
}
watch(() => props.selectedProjectId, loadProjectAgents, { immediate: true })

// 默认那个排第一 —— 常用路径是「点开、点第一项」，不用在列表里找。已停用的
// 不列：这个菜单是在给一个还没建的话题挑队友，正是停用要挡住的那件事。
const newTopicAgents = computed(() =>
  projectAgents.value.filter((a) => a.is_active !== false).sort((a, b) => Number(b.is_default) - Number(a.is_default))
)

// ----- Topic tree -----
// A flattened tree node: a topic plus its nesting depth, so the template can
// indent without recursion. Built from the flat list via parent_id.
interface TreeRow {
  topic: Topic
  depth: number
}

function inferKind(t: Topic): string {
  // Backend may already supply `kind`; otherwise derive it from the shape.
  const explicit = (t as Topic & { kind?: string }).kind
  if (typeof explicit === 'string' && explicit) return explicit
  return t.parent_id ? 'topic' : 'root'
}

// 边栏画的是房间。房间里派出去的活是**卡**，不是地点，看得见的地方是那个房间的
// 看板（总览那一格）和项目级那块板 —— 一行一个房间，一件活不再占一行。
const KIND_BADGE: Record<string, string> = {
  root: '全局',
  topic: '话题',
}

function kindLabel(t: Topic): string {
  return KIND_BADGE[inferKind(t)] ?? '话题'
}

// Status: only show when notable (archived / draft); active is implicit. Shown
// as a small neutral dot + text, never a colored chip.
function statusBadge(status: string): string | null {
  if (status === 'archived') return '已归档'
  if (status === 'draft') return '草稿'
  return null
}

const tree = computed<TreeRow[]>(() => {
  const all = props.topics
  const childrenOf = new Map<string | null, Topic[]>()
  for (const t of all) {
    const key = t.parent_id ?? null
    const arr = childrenOf.get(key) ?? []
    arr.push(t)
    childrenOf.set(key, arr)
  }

  const rows: TreeRow[] = []
  const seen = new Set<string>()

  const visit = (t: Topic, depth: number) => {
    if (seen.has(t.id)) return
    seen.add(t.id)
    rows.push({ topic: t, depth })
    for (const child of childrenOf.get(t.id) ?? []) visit(child, depth + 1)
  }

  // The root topic (本体) has its own pinned 全局 row above the list — show its
  // children (work topics) at depth 0, then any other top-level topics.
  const idSet = new Set(all.map((t) => t.id))
  const roots = all.filter((t) => !t.parent_id || !idSet.has(t.parent_id))
  const rootTopicNode = roots.find((t) => inferKind(t) === 'root')
  if (rootTopicNode) {
    seen.add(rootTopicNode.id)
    for (const child of childrenOf.get(rootTopicNode.id) ?? []) visit(child, 0)
  }
  for (const r of roots) {
    if (inferKind(r) !== 'root') visit(r, 0)
  }

  // Safety: append any orphans not reached (cycles / dangling parents).
  for (const t of all) if (!seen.has(t.id)) rows.push({ topic: t, depth: 0 })

  return rows
})

// 归档去向: archived topics leave the active tree and live in a collapsed
// 「已归档」 group at the bottom (newest archived first) — like Feishu's
// folded conversations. Non-archived children of an archived parent stay in
// the active list (their work isn't done).
//
// 但**活跟着它的房间走**：活只有 open/closed，没有「已归档」这个状态，所以房间
// 子话题有自己的归档状态：父话题归了、它还活着，那份活儿没做完，照旧留在活跃
// 列表里——只是父行没了，深度提到 0，免得被画到隔壁那棵树底下。
const activeTree = computed<TreeRow[]>(() => {
  // 深度按**留下来的那个父行**重新算，不沿用原树的：拍平的树里深度就是父子关系
  // 本身，中间少一层就得少一层缩进，否则缩进指着一行不存在的父行。
  const depths = new Map<string, number>()
  const rows: TreeRow[] = []
  for (const row of tree.value) {
    if (row.topic.status === 'archived') continue
    const parentId = row.topic.parent_id
    const parentDepth = parentId ? depths.get(parentId) : undefined
    const depth = parentDepth === undefined ? 0 : parentDepth + 1
    depths.set(row.topic.id, depth)
    rows.push(depth === row.depth ? row : { topic: row.topic, depth })
  }
  return rows
})
const archivedRows = computed<Topic[]>(() =>
  props.topics
    .filter((t) => t.status === 'archived' && inferKind(t) !== 'root')
    .sort((a, b) => (b.archived_at ?? '').localeCompare(a.archived_at ?? ''))
)
const archivedOpen = ref(false)

// ---- 话题级未读角标 (Feishu-style) ----
function unreadOf(id: string): number {
  return props.unreadMap?.[id] ?? 0
}
// The badge shows at most 99+ (a runaway count shouldn't stretch the row).
function countLabel(n: number): string {
  return n > 99 ? '99+' : String(n)
}
function unreadLabel(id: string): string {
  return countLabel(unreadOf(id))
}
// 私聊未读的总数——侧栏只说「有几条」，不说是谁。
const privateUnreadTotal = computed<number>(() =>
  Object.values(props.privateUnreadMap ?? {}).reduce((sum, n) => sum + n, 0)
)
// Unread hiding inside the collapsed archived group still deserves a hint.
const archivedUnread = computed<number>(() => archivedRows.value.reduce((sum, t) => sum + unreadOf(t.id), 0))

// ---- 折叠 ----
// 一个房间下面挂的是**它派出去的活**，不是子话题——房间之下不能再建房间。
// 范式跟底部的「已归档」分组一致（一个 chevron 收起一堆行），只是这里的开关
// 长在每一个有子话题的行上。行的可见性/未读聚合是纯逻辑，住在 lib/topicTree.ts
// 里（有单测），这里只管状态和落盘。
//
// 默认收起，展开是个动作。挂在一个房间下面的是它派出去的活，而活的去处是右边的
// Task Progress —— 一个跑久了的房间有近两百条，全都摊在主导航上等于把侧栏变成
// 一份没人读得完的清单。要看某个房间在干什么，点进去比在侧栏里滚要快。
// 按项目存 localStorage（而不是只放内存）：这个 rail 是主导航，每次刷新都要重展
// 一遍等于没有记住。存的是**展开的** id，所以新派出去的活天然是收起来的。
const expandedIds = ref<ReadonlySet<string>>(new Set<string>())
// `visibleRows` 问的是「哪些行是收起来的」，而我们记的是展开过的那些 —— 有孩子
// 的行里，没被展开过的就是收起来的。
const collapsedIds = computed<ReadonlySet<string>>(() => {
  const withChildren = new Set<string>()
  for (const t of props.topics) {
    if (t.parent_id && !expandedIds.value.has(t.parent_id)) withChildren.add(t.parent_id)
  }
  return withChildren
})
// 「其他话题」这一组展开没展开。默认折叠——这一整条改动的意义就在这里，所以它
// 也按项目落盘（键不在 = 折叠，见 lib/topicTree.ts）。
const othersOpen = ref(false)
watch(
  () => props.selectedProjectId,
  (pid) => {
    expandedIds.value = loadExpandedTopics(pid)
    othersOpen.value = loadOthersGroupOpen(pid)
  },
  { immediate: true }
)

// 当前选中话题的祖先链：这条路径无论祖先收没收起来都照常渲染，所以"人正待在
// 里面的那个话题"永远不会被折叠藏掉。用 reveal 而不是"自动展开"，是为了不把
// 用户自己设的折叠状态在导航时偷偷改写——离开之后那一支照旧是收起来的。
const selectedPath = computed(() => ancestorPathIds(props.topics, props.selectedTopicId))

// 状态查表：折叠聚合要按 id 问「这个话题在跑吗 / 在等人吗」，而拍平树里只留了
// id。走一遍 props.topics 建索引，别在每一行上做线性查找。
const topicById = computed(() => new Map(props.topics.map((t) => [t.id, t])))
function runningOf(id: string): boolean {
  return topicById.value.get(id)?.running === true
}
function awaitsOf(id: string): boolean {
  return topicById.value.get(id)?.awaits_me === true
}

// ---- 分组 (C2): 我参与的平铺，其他话题收进一个默认折叠的组 ----
// 判定住在 lib/topicTree.ts 里（纯函数 + 单测），这里只管接线、组的开关和落盘。
//
// 两组的**行是同一种形态**：同一段模板渲染，所以树形缩进、竖向引导线、16px 状态
// 槽、未读角标、hover 的 ⋯ 一个不少。折叠组只是把一批行收起来，不是换一种行。
//
const grouped = computed(() => partitionByRelevance(activeTree.value, isMyTopic))

function rowsOf(rows: readonly FlatRow<Topic>[]) {
  return visibleRows(rows, {
    collapsed: collapsedIds.value,
    reveal: selectedPath.value,
    unreadOf,
    runningOf,
    awaitsOf,
  })
}

const mineTree = computed(() => rowsOf(grouped.value.mine))
const othersTree = computed(() => rowsOf(grouped.value.others))

// 组头的未读聚合成**一个点**，不是数字：别人话题里有几条新消息与我无关，但"那边
// 有动静"值得知道。算的是整组（含组内自己收起来的子话题），所以点在不在，不受
// 组内折叠状态影响。
const othersUnread = computed<number>(() => grouped.value.others.reduce((sum, r) => sum + unreadOf(r.topic.id), 0))

// 选中的话题落在这一组里时，通往它的那条路径照常渲染——和折叠一个父话题时的
// reveal 一模一样：人正待在里面的那个话题永远不会被折叠藏掉，但收起来的其余部分
// 仍然是收起来的，组头的开关也仍然说的是用户自己设的那个值（不会变成一颗按了
// 没反应的按钮）。落盘的偏好一个字都不动，离开之后这一组照旧是收着的。
const othersHoldsSelected = computed(() =>
  props.selectedTopicId ? grouped.value.others.some((r) => r.topic.id === props.selectedTopicId) : false
)
const othersRendered = computed(() => {
  if (othersOpen.value) return othersTree.value
  if (!othersHoldsSelected.value) return []
  return othersTree.value.filter((r) => selectedPath.value.has(r.topic.id))
})

function toggleOthers() {
  othersOpen.value = !othersOpen.value
  saveOthersGroupOpen(props.selectedProjectId, othersOpen.value)
}

// 一次 v-for 走完两组，所以话题行的那段模板只存在一份——「形态一致」是结构保证
// 的，不是靠两处复制的模板保持同步。组头只有下面那一组有。
const railSections = computed(() => [
  { key: 'mine', label: '', head: false, count: 0, unread: 0, open: true, rows: mineTree.value },
  {
    key: 'others',
    label: '其他话题',
    head: grouped.value.others.length > 0,
    count: grouped.value.others.length,
    unread: othersUnread.value,
    // open 只管组头那个 chevron 的朝向 = 用户设的值；实际渲染哪些行看 rows。
    open: othersOpen.value,
    rows: othersRendered.value,
  },
])

// ---- 行左边那一个 16px 槽 ----
// 一个槽，按优先级换租客：有子话题 → 折叠开关；否则「等你处理」→ 琥珀点；
// 否则「芝士在跑」→ 绿呼吸点；都没有就空着（空槽仍占 16px，否则同层级的标题
// 左缘会参差）。原先 chevron 和状态点各占一槽，叶子行那 16px 是纯占位。
//
// 有子话题的行，chevron 顶掉了状态点，所以它自己带状态色——而且是「自己的 +
// 收起来的后代的」并成一个信号。收起来的父话题会把子话题的呼吸点整个藏掉是原
// 先的一个 bug（只有未读会聚合，"在跑" 不聚合），合槽顺手修掉它。展开一层就能
// 分清动静是本行的还是子话题的，扫侧栏时要的本来就是"这里面有动静"。
function rowAwaits(row: VisibleRow<Topic>): boolean {
  return row.topic.awaits_me === true || row.hiddenAwaits
}
function rowRunning(row: VisibleRow<Topic>): boolean {
  return row.topic.running === true || row.hiddenRunning
}
function toggleTitle(row: VisibleRow<Topic>): string {
  if (!row.collapsed) return '收起'
  if (row.hiddenAwaits) return '展开：里面有事等你处理'
  if (row.hiddenRunning) return '展开：芝士正在里面工作'
  return '展开'
}

function toggleCollapse(id: string) {
  const next = new Set(expandedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedIds.value = next
  saveExpandedTopics(props.selectedProjectId, next)
}

// The root topic (本体) — the pinned 「全局」 row at the top of the list. And
// the current project's display name, which is now pure identity: the project
// header is a label, not a button that opens a room nobody could guess at.
const rootTopic = computed<Topic | null>(() => props.topics.find((t) => inferKind(t) === 'root') ?? null)
const currentProjectName = computed<string>(
  () => props.projects.find((p) => p.id === props.selectedProjectId)?.name ?? '选择项目'
)

// Inline rename (pattern mirrors MyDevicesView's rename-in-place): a click on
// 重命名 in the row's ⋯ menu swaps the title span for a text field; enter/blur
// commits.
const renamingTopicId = ref<string | null>(null)
const draftTitle = ref('')

function startRename(t: Topic) {
  renamingTopicId.value = t.id
  draftTitle.value = t.title
}

function cancelRename() {
  renamingTopicId.value = null
}

function saveRename(t: Topic) {
  const title = normalizeTopicTitle(draftTitle.value, t.title)
  renamingTopicId.value = null
  if (title) emit('rename-topic', { id: t.id, title })
}

// 行操作收进一颗 ⋯ (C5): hover 只浮出一个入口，不再是三颗并排的按钮盖住标题
// 尾巴。菜单展开期间那一颗必须留在屏幕上——它是菜单的 activator，跟着 hover
// 一起消失的话，鼠标一移进菜单，菜单自己就塌了。
const actionsMenuFor = ref<string | null>(null)
function setActionsMenu(topicId: string, open: boolean) {
  actionsMenuFor.value = open ? topicId : null
}

// 项目文档 (C4): 章程 / 决策记录 / 周报集 / 记忆 在侧栏只占一行，点开进章程；
// 四选一的切换长在 ProjectDocsView 页面里（一 kind 一址，URL 照旧会变）。所以
// 这一行在任何一种文档打开时都是选中态。
const onDocs = computed(() => !!props.activeDocs)

// 一列图标，一列文字。侧栏里每一行的左侧都是「8px 起 + 一个 16px 槽」——话题行
// 是折叠开关/状态点，置顶行和项目文档是自己的图标。所以缩进
// 只有一个值了（折叠开关不再单独占一列，见上面的合槽说明）。写在 style 上而不是
// scoped class 里：Vuetify 的 `.v-list--nav .v-list-item` 内边距比单个 scoped
// 类更特化，话题行本来也是这么压住它的。
const ROW_INDENT = { paddingInlineStart: '8px' }
</script>

<template>
  <component
    :is="page ? 'div' : SecondaryNavigation"
    :width="page ? undefined : width ?? 280"
    :custom-class="page ? undefined : 'topic-rail'"
    :class="page ? 'topic-rail topic-rail--page' : undefined"
  >
    <!-- Drag handle on the right edge to resize the rail. 整页形态下没有可拖的
         宽度——它占满内容区。 -->
    <div v-if="!page" class="rail-resizer" title="拖动调整宽度" @mousedown="startResize" />
    <!-- 两段式: 头固定 / 下面唯一滚动。原来还有第三段（尾固定的私聊栏），它
         撤掉了：私聊的未读改挂在「成员」那一行上，而那一行在头下面的置顶组里，
         本来就不随话题列表滚。 -->
    <div class="d-flex flex-column fill-height">
      <!-- 项目头 = 标识 + 菜单，整块可点。48px 基线 (.sidebar-header) 和首页
           侧栏头、内容区 PageHeader 共用，三条标题线才落在同一水平上。
           activator 用 <button> 而不是 <div>（SpaceSidebar 那个先例是裸 div）：
           整块可点就得整块可聚焦、能用回车/空格打开，否则键盘用户够不着项目
           设置。右边的 chevron 只是"这里能展开"的指示，不再是唯一的靶子——所以
           它是 v-icon 不是 v-btn，按钮套按钮既非法也抢焦点。 -->
      <!-- 整页形态（手机上的话题列表）下这一行不长在页面上，而是填进顶栏那一格：
           手机上只有一条顶栏，页面自己再画一条就是两条横条一上一下写同类的东西。 -->
      <Teleport to="#app-bar-slot" :disabled="!page">
        <v-menu location="bottom end">
          <template #activator="{ isActive, props: menuProps }">
            <button
              v-bind="menuProps"
              type="button"
              class="sidebar-header sidebar-header-menu rail-header"
              :class="{ 'sidebar-header-menu-active': isActive, 'rail-header--bar': page }"
              title="项目菜单"
            >
              <!-- 名字自己留一个 title：它是省略号截断的，鼠标停在名字上要能看到全名。 -->
              <span class="rail-header__name" :title="currentProjectName">{{ currentProjectName }}</span>
              <v-icon class="rail-header__caret" size="18" icon="mdi-chevron-down" />
            </button>
          </template>
          <v-list density="compact" nav max-height="60vh">
            <!-- 整页形态下这个菜单是**唯一**能换项目的地方：一个项目一格的那条
                 竖 rail 只在桌面渲染，底栏「工作区」那一格只落到一个项目，于是
                 手机上进了一个项目就再也走不到别的项目去。桌面不列——rail 已经
                 是那个入口，同一件事有两个入口只会让人猜哪个才算数。 -->
            <template v-if="page && projects.length > 1">
              <v-list-subheader class="t-eyebrow">切换项目</v-list-subheader>
              <v-list-item
                v-for="p in projects"
                :key="p.id"
                :active="p.id === selectedProjectId"
                rounded="lg"
                @click="openProject(p.id)"
              >
                <template #prepend>
                  <span class="private-avatar-slot me-3">
                    <span class="dm-avatar project-avatar" :style="{ backgroundColor: avatarColor(p.name) }">{{
                      avatarInitial(p.name)
                    }}</span>
                  </span>
                </template>
                <v-list-item-title class="t-body">{{ p.name }}</v-list-item-title>
              </v-list-item>
              <v-divider class="my-1" />
            </template>
            <v-list-item
              prepend-icon="mdi-cog-outline"
              title="项目设置"
              :disabled="!selectedProjectId"
              @click="openProjectPage('project-settings')"
            />
          </v-list>
        </v-menu>
      </Teleport>

      <!-- 中段：这个侧栏里唯一会滚的东西 -->
      <div class="rail-scroll flex-grow-1 overflow-y-auto">
        <template v-if="!selectedProjectId">
          <div class="t-body c-muted pa-4">先选择一个项目</div>
        </template>
        <template v-else>
          <!-- 置顶行 (C1): 全局房间 + 总览 + 日历。和话题行同一种语法——同图标
               槽、同缩进基准、同选中态、同未读角标，所以「点它会发生什么」不用
               另学一遍。 -->
          <v-list density="compact" nav class="py-0 pt-1">
            <v-list-item
              v-if="rootTopic"
              :active="rootTopic.id === selectedTopicId"
              rounded="lg"
              class="nav-row pinned-row"
              :class="{ 'is-active': rootTopic.id === selectedTopicId }"
              :style="ROW_INDENT"
              @click="emit('select-topic', rootTopic.id)"
              @mouseenter="emit('hover-topic', rootTopic.id)"
              @mouseleave="emit('leave-topic')"
            >
              <template #prepend>
                <!-- 置顶行的槽住的是它自己的图标：# / 总览 / 日历 三个各不相同，
                     是能区分行的信息，不是话题行上那种每行一模一样的装饰。 -->
                <span class="row-slot">
                  <v-icon
                    size="16"
                    class="row-glyph"
                    :class="{ 'row-glyph--unread': unreadOf(rootTopic.id) > 0 }"
                    icon="mdi-pound"
                  />
                </span>
              </template>
              <v-list-item-title :class="{ 'title-unread': unreadOf(rootTopic.id) > 0 }">全局</v-list-item-title>
              <template #append>
                <span v-if="unreadOf(rootTopic.id) > 0" class="unread-badge">{{ unreadLabel(rootTopic.id) }}</span>
              </template>
            </v-list-item>

            <v-list-item
              v-for="p in projectPages"
              :key="p.key"
              :active="route.name === p.key"
              rounded="lg"
              class="nav-row pinned-row"
              :class="{ 'is-active': route.name === p.key }"
              :style="ROW_INDENT"
              @click="openProjectPage(p.key)"
              @mouseenter="hoverProjectPage(p.key)"
              @mouseleave="cancelPrefetch()"
            >
              <template #prepend>
                <span class="row-slot">
                  <v-icon size="16" class="row-glyph" :icon="p.icon" />
                </span>
              </template>
              <v-list-item-title>{{ p.label }}</v-list-item-title>
              <!-- 私聊的未读挂在「成员」这一行上。私聊那一栏撤掉之后，这是
                   「有人找你」在主导航上唯一会亮的地方，所以它必须在这里；进了
                   成员页才精确到是谁（每个人的私聊按钮上各带各的）。 -->
              <template v-if="p.key === 'project-members' && privateUnreadTotal > 0" #append>
                <span class="unread-badge">{{ countLabel(privateUnreadTotal) }}</span>
              </template>
            </v-list-item>
          </v-list>

          <v-divider class="mx-3 my-1" />

          <div class="t-eyebrow side-subhead side-subhead--row">
            <span>话题</span>
            <!-- 建话题时就把房间交给谁定下来。队友列表拿不到时（旧环境）退回
                 一键直建，不让侧栏的主要动作被一个可选接口卡住。 -->
            <v-menu v-if="projectAgents.length" location="bottom end">
              <template #activator="{ props: menu }">
                <v-btn v-bind="menu" icon="mdi-plus" size="x-small" variant="tonal" color="primary" title="新建话题" />
              </template>
              <v-list density="compact" min-width="220">
                <v-list-subheader>交给哪个 AI 队友</v-list-subheader>
                <v-list-item v-for="a in newTopicAgents" :key="a.id ?? a.handle" @click="newTopic(a.id)">
                  <template #prepend>
                    <v-icon size="small" icon="mdi-robot-outline" />
                  </template>
                  <v-list-item-title>{{ a.display_name }}</v-list-item-title>
                  <template v-if="a.is_default" #append>
                    <span class="t-meta c-muted">默认</span>
                  </template>
                </v-list-item>
              </v-list>
            </v-menu>
            <v-btn
              v-else
              icon="mdi-plus"
              size="x-small"
              variant="tonal"
              color="primary"
              title="新建话题"
              @click="newTopic()"
            />
          </div>

          <LoadingSkeleton v-if="loadingTopics" variant="list" class="rail-skel" />

          <template v-else>
            <!-- 一组都不相关的时候（刚进项目、还没参与任何话题），上组是空的。
                 说清楚"空的是这一组，不是这个项目"，否则下面那个折叠组会像个谜。 -->
            <v-list v-if="mineTree.length === 0 && grouped.others.length > 0" density="compact" nav class="py-0">
              <v-list-item class="c-faint t-body"> 暂无与你相关的话题 </v-list-item>
            </v-list>

            <!-- 分组 (C2): 两组走同一段模板。上组直接平铺；下组「其他话题」多一个
                 组头、默认收起。行的形态两组完全一致——见 .group-toggle 的注释。

                 组头长在 <v-list> **外面**，一组一个 <v-list>：`.v-list--nav` 自带
                 8px 的 padding-inline，组头搁在列表里就会比列表外的「已归档」组头
                 右移 8px——两个同款组头一上一下差着一级缩进，「其他话题」读起来像
                 上一条话题的子项。用负 margin 抵掉那 8px 只是把它藏起来，组头本来
                 就不是列表项。 -->
            <template v-for="section in railSections" :key="section.key">
              <button v-if="section.head" type="button" class="group-toggle" @click="toggleOthers">
                <v-icon size="15" class="c-faint">
                  {{ section.open ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
                </v-icon>
                <span class="t-eyebrow">{{ section.label }}</span>
                <span class="group-count">{{ section.count }}</span>
                <!-- 收起来时聚合成一个点，不是数字：别人话题里有几条与我无关，
                     但"那边有动静"值得知道。 -->
                <span
                  v-if="!section.open && section.unread > 0"
                  class="unread-badge unread-badge--dot"
                  title="其他话题里有新消息"
                />
              </button>

              <v-list density="compact" nav class="py-0">
                <v-list-item
                  v-for="row in section.rows"
                  :key="row.topic.id"
                  :active="row.topic.id === selectedTopicId"
                  rounded="lg"
                  class="topic-row"
                  :class="{
                    'is-active': row.topic.id === selectedTopicId,
                    'is-sub': row.depth > 0,
                    'is-menu-open': actionsMenuFor === row.topic.id,
                  }"
                  :style="{
                    paddingInlineStart: 8 + row.depth * 20 + 'px',
                    '--guide-x': 16 + (row.depth - 1) * 20 + 'px',
                  }"
                  @click="emit('select-topic', row.topic.id)"
                  @mouseenter="emit('hover-topic', row.topic.id)"
                  @mouseleave="emit('leave-topic')"
                >
                  <!-- 干净行：左边只有一个 16px 槽（状态，或顶替它的折叠开关），
                       身份靠标题本身，种类标签不要（缩进表达层级），操作 hover 才浮现。
                       原先这里还有一颗每行都一样的装饰图标——同一层级里人人相同的
                       标记区分不了任何东西，删掉了。 -->
                  <template #prepend>
                    <button
                      v-if="row.hasChildren"
                      type="button"
                      class="row-slot subtree-toggle"
                      :class="{
                        'subtree-toggle--awaits': rowAwaits(row),
                        'subtree-toggle--running': !rowAwaits(row) && rowRunning(row),
                      }"
                      :title="toggleTitle(row)"
                      :aria-expanded="!row.collapsed"
                      @click.stop="toggleCollapse(row.topic.id)"
                    >
                      <v-icon size="15">
                        {{ row.collapsed ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
                      </v-icon>
                    </button>
                    <!-- 等你处理：有点名给你的验收卡，或有 @你 的未读。排在"在跑"
                         前面——芝士在忙是它的事，等你做事才是你的事。 -->
                    <span v-else-if="row.topic.awaits_me" class="row-slot">
                      <span class="await-dot" title="有事等你处理" />
                    </span>
                    <!-- 芝士还在这个话题里工作：呼吸点，人凭它判断啥时候该派下一个
                         任务——和归档/采纳状态无关，只是这会儿有没有跑完。 -->
                    <span v-else-if="row.topic.running" class="row-slot">
                      <span class="running-dot" title="芝士正在这个话题里工作" />
                    </span>
                    <span v-else class="row-slot" />
                  </template>
                  <v-list-item-title class="d-flex align-center topic-title">
                    <v-text-field
                      v-if="renamingTopicId === row.topic.id"
                      v-model="draftTitle"
                      density="compact"
                      variant="outlined"
                      hide-details
                      autofocus
                      :maxlength="TOPIC_TITLE_MAX_LENGTH"
                      class="rename-field"
                      @click.stop
                      @keyup.enter="saveRename(row.topic)"
                      @keyup.esc="cancelRename()"
                      @blur="saveRename(row.topic)"
                    />
                    <template v-else>
                      <!-- 「该谁动」的色点，和看板上那一列同一个颜色、同一个形状
                           （`lib/board.ts` 是唯一的来源）。侧栏和看板对不上的话，
                           人就得在两块屏幕之间自己做一次翻译。
                           后端没给 `presentation` 就不画——不在前端另算一个顶上。 -->
                      <span
                        v-if="row.topic.presentation"
                        class="board-dot"
                        :style="columnDotStyle(row.topic.presentation.column)"
                        :title="row.topic.presentation.display_status"
                      />
                      <span class="text-truncate" :class="{ 'title-unread': row.unreadTotal > 0 }">{{
                        row.topic.title
                      }}</span>
                      <!-- 收起来了就说清楚收了多少——「这里还有内容」得看得见。 -->
                      <span
                        v-if="row.collapsed && row.hiddenCount > 0"
                        class="subtree-count ms-2"
                        :title="`收起了 ${row.hiddenCount} 项`"
                        >{{ countLabel(row.hiddenCount) }}</span
                      >
                      <span
                        v-if="statusBadge(row.topic.status)"
                        class="d-inline-flex align-center ga-1 c-faint topic-status ms-2"
                      >
                        <span class="status-dot status-dot--warn" />
                        {{ statusBadge(row.topic.status) }}
                      </span>
                    </template>
                  </v-list-item-title>
                  <template #append>
                    <!-- 折叠不能把"有新消息"吞掉：收起来的后代的未读加到本行上。 -->
                    <span
                      v-if="row.unreadTotal > 0"
                      class="unread-badge"
                      :title="row.hiddenUnread > 0 ? `含收起的子话题 ${row.hiddenUnread} 条新消息` : undefined"
                      >{{ countLabel(row.unreadTotal) }}</span
                    >
                    <!-- hover 浮出的操作入口：一颗 ⋯，绝对定位覆盖行尾，不占布局宽度 -->
                    <div class="row-actions" @click.stop>
                      <v-menu
                        :model-value="actionsMenuFor === row.topic.id"
                        location="bottom end"
                        @update:model-value="(open: boolean) => setActionsMenu(row.topic.id, open)"
                      >
                        <template #activator="{ props: menuProps }">
                          <v-btn
                            v-bind="menuProps"
                            icon="mdi-dots-horizontal"
                            size="small"
                            variant="text"
                            density="comfortable"
                            title="更多操作"
                            class="row-actions__btn"
                          />
                        </template>
                        <v-list density="compact" nav>
                          <v-list-item
                            prepend-icon="mdi-pencil-outline"
                            title="重命名"
                            @click="startRename(row.topic)"
                          />
                          <v-list-item
                            prepend-icon="mdi-archive-arrow-down-outline"
                            title="归档"
                            @click="emit('archive-topic', row.topic.id)"
                          />
                        </v-list>
                      </v-menu>
                    </div>
                  </template>
                </v-list-item>
              </v-list>
            </template>

            <v-list v-if="activeTree.length === 0" density="compact" nav class="py-0">
              <v-list-item class="c-faint t-body"> 暂无话题 </v-list-item>
            </v-list>
          </template>

          <!-- 归档去向: collapsed 已归档 group at the bottom of the topic list.
               Archived topics leave the active tree and land here (newest
               first), so done work stops crowding the rail. -->
          <template v-if="archivedRows.length">
            <button type="button" class="group-toggle archived-toggle" @click="archivedOpen = !archivedOpen">
              <v-icon size="15" class="c-faint">
                {{ archivedOpen ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
              </v-icon>
              <span class="t-eyebrow">已归档</span>
              <span class="group-count">{{ archivedRows.length }}</span>
              <span
                v-if="!archivedOpen && archivedUnread > 0"
                class="unread-badge unread-badge--dot"
                title="归档话题里有新消息"
              />
            </button>
            <v-list v-if="archivedOpen" density="compact" nav class="py-0">
              <v-list-item
                v-for="t in archivedRows"
                :key="t.id"
                :active="t.id === selectedTopicId"
                rounded="lg"
                class="topic-row topic-row--archived"
                :class="{ 'is-active': t.id === selectedTopicId }"
                @click="emit('select-topic', t.id)"
                @mouseenter="emit('hover-topic', t.id)"
                @mouseleave="emit('leave-topic')"
              >
                <template #prepend>
                  <v-icon size="16" class="me-1 c-faint" icon="mdi-archive-outline" />
                </template>
                <v-list-item-title class="d-flex align-center ga-2 topic-title">
                  <span class="text-truncate">{{ t.title }}</span>
                  <span class="kind-text">{{ kindLabel(t) }}</span>
                </v-list-item-title>
                <template #append>
                  <span v-if="unreadOf(t.id) > 0" class="unread-badge me-1">
                    {{ unreadLabel(t.id) }}
                  </span>
                  <v-btn
                    icon="mdi-archive-arrow-up-outline"
                    size="small"
                    variant="text"
                    density="comfortable"
                    title="取消归档"
                    class="split-btn"
                    @click.stop="emit('unarchive-topic', t.id)"
                  />
                </template>
              </v-list-item>
            </v-list>
          </template>

          <v-divider class="mx-3 my-1" />

          <!-- 项目文档 (C4): 一行。四种文档的切换在页面里，不在这条黄金位上。 -->
          <v-list density="compact" nav class="py-0">
            <v-list-item
              :active="onDocs"
              rounded="lg"
              class="nav-row docs-row"
              :class="{ 'is-active': onDocs }"
              :style="ROW_INDENT"
              prepend-icon="mdi-file-document-outline"
              title="项目文档"
              @click="emit('select-docs', 'charter')"
            />
          </v-list>
        </template>
      </div>

      <!-- 新建项目 moved to the project rail's + (App.vue) — one affordance,
           Discord-style. The create-project emit stays for API compatibility. -->
    </div>
  </component>
</template>

<style scoped>
/* Right-edge resize handle (sits on top of the drawer's border). */
/* 整页形态：占满内容区，不画抽屉那条右边线。 */
.topic-rail--page {
  width: 100%;
  height: 100%;
  background: var(--canvas);
}
.rail-resizer {
  position: absolute;
  top: 0;
  right: -3px;
  bottom: 0;
  width: 11px;
  cursor: col-resize;
  z-index: 4;
}
/* A thin visible handle centered in the wider (grabbable) hit area. */
.rail-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  right: 3px;
  width: 2px;
  background: transparent;
  transition: background 0.12s ease;
}
.rail-resizer:hover::after {
  background: var(--accent);
}
.side-subhead {
  padding: 14px 16px 4px;
}
.side-subhead--row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-block: 6px 4px;
  padding-inline-end: 8px;
}

/* 项目头：整块是菜单的 activator。高度和内边距来自全局 .sidebar-header
   (48px 基线)。
 *
 * 底部那条分隔线必须在这里再声明一遍，不能指望全局 .sidebar-header 那条。
 * 原因是 style.css 的 `button:not(.v-btn) { border: none }`——那条选择器权重是
 * (0,1,1)，压过 .sidebar-header 的 (0,1,0)，而这条 rail 是四个侧栏里唯一把
 * .sidebar-header 放在 <button> 上的（首页/空间/设置都是 <div>），所以**只有
 * 工作台**这条线被抹掉了，其余三个照常显示。那条 reset 自己的注释也写明了这个
 * 约定：「buttons that declare their own border override this」。
 *
 * 颜色和 .sidebar-header / PageHeader 完全一致（Vuetify 那对 border token），
 * 不是 --line/--line-2：目标是和右边内容区顶栏那条线同款同高，能接成一条。
 * 删掉这一行，线就会静默消失，而且沙箱里跑不了渲染、任何测试都抓不到。 */
.rail-header {
  width: 100%;
  /* 它必须退出收缩：下面那段 .rail-scroll 的 flex-basis 是 auto = 那一长列话题
     的内容高度，几十个话题就足以把整列撑得比侧栏高。弹性盒于是按各自 basis 分摊
     收缩量，这一条虽只有 48px 也照分，一路被压到自己的最小内容高度（8+8 内边距
     + 一行字 ≈ 38px）为止——右边内容区顶栏钉死在 48px，两条分隔线就再也接不上。
     下面那段自己有 overflow-y:auto，min-height 解析为 0，该吸收收缩量的本来就
     是它。 */
  flex: none;
  border: 0;
  border-block-end: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: none;
  font: inherit;
  color: inherit;
  text-align: start;
  cursor: pointer;
}
/* 填进顶栏的那一份不画自己的高度和底线——那两样归顶栏。 */
.rail-header--bar {
  height: 100%;
  border-block-end: 0;
  padding-inline: 0;
}
.rail-header:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.rail-header__caret {
  flex: none;
  color: var(--muted);
}
.rail-header__name {
  min-width: 0;
  /* 15/600 = .t-title，和话题头、手机顶栏同一号：这三条横条在屏幕上是接着的。 */
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Unread: the row's title carries the signal. */
.title-unread {
  font-weight: 650;
}

/* Topic / nav rows: title ink, quiet by default. */
.topic-row :deep(.v-list-item-title),
.nav-row :deep(.v-list-item-title) {
  font-size: 13.5px;
  color: var(--text);
}
.topic-title {
  color: var(--text);
}
.rename-field {
  max-width: 220px;
}
.rename-field :deep(.v-field__input) {
  padding-top: 2px;
  padding-bottom: 2px;
  min-height: 28px;
  font-size: 13.5px;
}

/* 三态：静默（透明，露出 rail 的 --canvas）/ hover --fill-2 / 选中 --line-2。
   没有琥珀左竖条——选中态靠底色和字重就够了（Slack/Discord 的行选中态也只是
   底色），左条纹在这套设计语言里只留给引用块和树的结构线。

   为什么不是 --fill：--fill 的定义就是「hover on --surface」，而这条 rail 现在
   坐在 --canvas 上。浅色主题里 --fill #f4f5f7 压在 --canvas #f7f8fa 上对比度
   只有 1.027:1（3/255），等于看不见；hover 还用同一个值，两态也彼此不可分。

   为什么两个主题共用一组 token：三档明暗次序在两个主题间是反的（浅色
   surface > canvas > fill > fill-2，深色 fill-2 > fill > surface > canvas），
   所以选的依据是**对 --canvas 的对比度**而不是名字。--fill-2 → --line-2 这一
   对在两个主题下都是单调递增地离开 canvas：
     浅色 canvas #f7f8fa：fill-2 1.083:1 → line-2 1.208:1（两者之间 1.115:1）
     深色 canvas #141517：fill-2 1.301:1 → line-2 1.701:1（两者之间 1.308:1）
   所以不需要任何 [data-theme='dark'] 分支。--line-2 是拿来当底色用的，它在
   ramp 上正好是"比 fill-2 再深一档"的那个中性色，不是新造的颜色。
   Kill Vuetify's default active overlay so no amber bleeds in. */
.topic-row.is-active,
.nav-row.is-active {
  background: var(--line-2);
}
.topic-row.is-active :deep(.v-list-item__overlay),
.nav-row.is-active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}
.topic-row.is-active :deep(.v-list-item-title),
.nav-row.is-active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}
.topic-row.is-active :deep(.v-icon),
.nav-row.is-active :deep(.v-icon) {
  color: var(--muted) !important;
}

/* 「该谁动」的色点。颜色和形状由 `lib/board.ts` 一处给出（内联样式），这里只管
   尺寸和位置 —— scoped 样式进不了别的组件，颜色写在这儿就意味着看板和房间总览
   各有一份，而这颗点存在的全部意义就是三处说的是同一件事。 */
.board-dot {
  flex: none;
  width: 8px;
  height: 8px;
  margin-inline-end: 6px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
.topic-status {
  font-size: 11.5px;
}

/* 未读角标 (Feishu-style): a compact red pill with the count. */
.unread-badge {
  flex: none;
  pointer-events: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 未读计数 = 裸的琥珀数字（owner 定的醒目色），行里唯一常驻的右对齐元素。
     形态历经红圆/石墨药丸被否——干净的行 + 一个琥珀数字才是答案。 */
  background: none;
  color: var(--accent);
  margin-left: 6px;
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}
/* Collapsed 已归档 header: just a dot hint, not a count. */
.unread-badge--dot {
  width: 6px;
  height: 6px;
  padding: 0;
  border-radius: 50%;
  background: var(--muted);
}

/* 收起来的一组话题的组头：底部的「已归档」，以及话题列表里的「其他话题」。
   两处共用一种形态——同一条侧栏里"一组被收起来的话题"只能有一种读法。
   注意组头长这样，不代表**组里的行**也换一种形态：「其他话题」里的行走的是
   上面那套完整的 .topic-row（树形缩进、状态槽、未读、hover 的 ⋯）。 */
.group-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: calc(100% - 16px);
  margin: 2px 8px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  transition: background 0.12s ease;
}
.group-toggle:hover {
  background: var(--fill);
}
.group-toggle .t-eyebrow {
  padding: 0;
}
.group-count {
  font-size: 11px;
  color: var(--faint);
  background: var(--fill);
  border-radius: 8px;
  padding: 1px 6px;
}
/* 话题还在路上时，先把行的形状画出来（LoadingSkeleton）。这条 rail 的底是
   --canvas，骨架默认那档 --fill-2 压上去只有 1.083:1，等于什么都没画；--line-2 是
   这条 rail 上「再离底一档」的那个值（选中行用的也是它），在两个主题下都看得见。 */
.rail-skel {
  --skel-bone: var(--line-2);
}

/* Archived rows read as "done": slightly dimmed titles. */
.topic-row--archived :deep(.v-list-item-title) {
  color: var(--muted);
}

/* Hover-only action overlay (评审处方): absolutely positioned over the row's
   tail, zero layout width — the title gets the full rail. Hover detection is
   the WHOLE row (the old flicker came from hovering the buttons themselves),
   and a gradient shoulder fades the title out under the buttons. */
.topic-row {
  position: relative;
  min-height: 36px;
  margin-block: 2px;
}
.topic-row :deep(.v-list-item__content) {
  padding-block: 0;
}
/* 核心修正：Vuetify 的 prepend spacer 默认 ~32px，把图标和标题隔出一条鸿沟，
   稀释了一切缩进关系。压到 8px，缩进的台阶才立得起来。
   nav-row (置顶行/项目文档) 必须共用同一套：否则图标虽同列，文字却各自缩进
   （话题 24px、项目文档 56px），两列文字对不齐。 */
.topic-row :deep(.v-list-item__spacer),
.nav-row :deep(.v-list-item__spacer) {
  width: 8px !important;
}
/* 图标槽统一成 16px 定宽方块：话题用 size=16 的 v-icon，项目文档用
   prepend-icon（默认 24）。锁死 prepend 里图标的字号与槽宽，icon-left 与
   text-left 才能双双成列。 */
.topic-row :deep(.v-list-item__prepend),
.nav-row :deep(.v-list-item__prepend) {
  align-items: center;
}
.nav-row :deep(.v-list-item__prepend > .v-icon) {
  font-size: 16px;
  width: 16px;
  height: 16px;
  margin: 0;
}
/* 头像槽：定宽 16px、与图标同列；18px 头像在其中居中、略微溢出，视觉大小与
   话题/项目文档那一列的图标持平，头像左缘落在同一图标列、文字左缘落在同一
   文字列。项目切换菜单里的项目头像用的就是它。 */
.private-avatar-slot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex: none;
  overflow: visible;
}
/* 首字母头像：人的（.dm-avatar，圆）和项目的（.project-avatar，方）同一套底子，
   18px，图标列和文字列才对得齐。 */
.dm-avatar,
.project-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  /* The --fill here is only the pre-paint placeholder: the real ground is
     avatarColor() bound inline in the template, a fixed hsl that is the same in
     both themes — so the initial on it stays a literal #fff. */
  background: var(--fill);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}
/* 项目头像：和人的头像同一个底子（.dm-avatar），只换形状——方头像，和桌面那条
   竖 rail 上一个项目一格的画法是同一种语言。人是靠方/圆区分「这是个项目」还是
   「这是个人」的，都画成圆的就混了。 */
.project-avatar {
  border-radius: var(--radius-sm);
}
/* 置顶行的图标：# / 总览 / 日历，三个各不相同所以留着；未读转琥珀。 */
.row-glyph {
  color: var(--faint);
}
.row-glyph--unread {
  color: var(--accent);
}

/* 行左边那一个 16px 定宽槽。所有行共用（话题行的状态/开关、置顶行的图标），
   所以图标列和文字列在整条侧栏上都成列。空槽也占满 16px：同层级的标题左缘
   必须齐，参差比多一点留白难看得多。 */
.row-slot {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
}

/* 子话题折叠开关：占同一个槽，顶替状态点。 */
.subtree-toggle {
  border-radius: var(--radius-sm);
  cursor: pointer;
}
/* 开关不给底色 hover：它坐在的行底色有三档（静默/hover/选中），任何一个固定
   的底色 token 都会在其中某一档上糊掉。改成图标本身变深——16px 的小控件靠
   墨色变化做反馈就够，也不用跟行底色抢层次。 */
.subtree-toggle :deep(.v-icon) {
  color: var(--faint);
}
.subtree-toggle:hover :deep(.v-icon) {
  color: var(--text);
}
/* 收起来的父话题会把子话题的状态整个藏掉（未读会聚合，"在跑"和"等你"原先不会）
   ——开关自己带聚合色补上：琥珀 = 里面有事等你，绿 = 里面芝士在跑。展开着的行
   则表示本行自己的状态，因为槽被开关占了。hover 不改这两个颜色，状态优先于反馈。 */
/* !important 是被逼的，不是偷懒：上面 .topic-row.is-active :deep(.v-icon) 为了
   压住 Vuetify 的琥珀 active overlay 用了 !important，选中的那一行会连带把这里
   的状态色刷成 --muted——正好是"这一行收起来了、里面有事等你"最该看见的时候。 */
.subtree-toggle--awaits :deep(.v-icon),
.subtree-toggle--awaits:hover :deep(.v-icon) {
  color: var(--accent) !important;
}
.subtree-toggle--running :deep(.v-icon),
.subtree-toggle--running:hover :deep(.v-icon) {
  color: var(--ok) !important;
}

/* 等你处理：琥珀实心点 + 一圈 accent-wash 光晕。跟绿色呼吸点靠三个通道区分
   （颜色 / 有没有光晕 / 动不动），不是只靠颜色——红绿色觉障碍下也分得开。 */
.await-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-wash);
}
/* 收起来了收了几个——形态沿用「已归档」那颗计数丸。 */
.subtree-count {
  flex: none;
  font-size: 11px;
  color: var(--faint);
  background: var(--fill);
  border-radius: 8px;
  padding: 1px 6px;
  font-variant-numeric: tabular-nums;
}
/* 呼吸点：芝士还在这一轮里工作，跟归档/采纳状态无关。
   原先它绝对定位挂在装饰图标的右下角，所以需要一圈底色描边把自己从图标上抠
   出来。现在它独占那个槽、周围没有东西可压，描边就只剩害处了——行底色有三档，
   固定取 --canvas 的描边在 hover 和选中的行上会露出一圈错色的边。 */
.running-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
  animation: running-dot-pulse 1.6s ease-in-out infinite;
}
@keyframes running-dot-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.7);
  }
}
/* 分身组的竖向引导线：把一串子话题挂在父话题下（Linear/Notion 树形手法）。
   这是结构线，不是强调条——左条纹禁令不管它。 */
.topic-row.is-sub::before {
  content: '';
  position: absolute;
  left: var(--guide-x, 24px);
  top: -3px;
  bottom: -3px;
  width: 1px;
  background: var(--line-2);
}
.topic-row:hover,
.nav-row:hover {
  background: var(--fill-2);
}
/* 选中的行 hover 不能倒退回 hover 档——否则鼠标一扫过，选中态反而变浅。 */
.topic-row.is-active:hover,
.nav-row.is-active:hover {
  background: var(--line-2);
}
.title-unread {
  color: var(--text);
}

.row-actions {
  position: absolute;
  right: 5px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 3px;
  opacity: 0;
  pointer-events: none;
  /* 有意为之的浮动工具条（Linear 手法）：白底+细边+微影，
     在任何行底色上都成立——不再试图和行底色融为一体。 */
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  transition: opacity 0.1s ease;
  color: var(--muted);
}
/* 工具条里的那颗 ⋯ 要有自己的悬停反馈——否则不像能按的东西。
   舒适可点，但必须小于行高（~36px）：25px 按钮 + 16px 图标，稳稳落在行内。 */
.row-actions :deep(.v-btn) {
  width: 25px;
  height: 25px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.row-actions :deep(.v-btn .v-icon) {
  font-size: 16px;
}
.row-actions :deep(.v-btn:hover) {
  background: var(--fill);
  color: var(--text);
}
/* 取消归档按钮与 ⋯ 同属一个按钮家族：同样的 25px 方盒、7px 圆角、
   16px 图标、琥珀强调 + hover 反馈，避免归档区里出现一颗尺寸/配色不一致的按钮。 */
.split-btn {
  width: 25px;
  height: 25px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--accent);
}
.split-btn :deep(.v-icon) {
  font-size: 16px;
}
.split-btn:hover {
  background: var(--fill);
  color: var(--accent);
}
/* 触摸屏没有 hover，:focus-within 又要先聚焦——这两条规则加起来，⋯ 菜单在手机上
   根本摸不到。所以在没有 hover 能力的设备上它常驻。按输入方式判断，不按视口宽度：
   带触摸屏的笔记本两样都对。 */
@media (hover: none) {
  .row-actions {
    opacity: 1;
    pointer-events: auto;
  }
  .topic-row .unread-badge {
    opacity: 1;
  }
}
/* 菜单展开时那颗 ⋯ 必须留着：它是菜单的 activator，跟 hover 一起消失的话
   鼠标一离开行、菜单就没了根。 */
.topic-row:hover .row-actions,
.topic-row:focus-within .row-actions,
.topic-row.is-menu-open .row-actions {
  opacity: 1;
  pointer-events: auto;
}
/* While the actions are out, the count steps aside (they share the tail). */
.topic-row:hover .unread-badge,
.topic-row:focus-within .unread-badge,
.topic-row.is-menu-open .unread-badge {
  opacity: 0;
}
</style>
