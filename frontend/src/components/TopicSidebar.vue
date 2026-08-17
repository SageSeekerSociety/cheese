<script setup lang="ts">
import type { TopicSortField, TopicSortOrder } from '../api'
import type { Project, ProjectMemberRow, Topic } from '../cx_types'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { relTime } from '../lib/relTime'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '../lib/topicTitle'
import { ancestorPathIds, loadCollapsedTopics, saveCollapsedTopics, visibleRows } from '../lib/topicTree'
import { avatarColor } from '../utils/avatar'

import CheeseAvatar from './CheeseAvatar.vue'

const props = defineProps<{
  projects: Project[]
  selectedProjectId: string | null
  topics: Topic[]
  selectedTopicId: string | null
  loadingTopics: boolean
  // True when the 私聊 (1:1 with 芝士) entry is the active main view.
  privateActive: boolean
  // Project roster for the 私聊 DM list (each OTHER member = a person to DM).
  members?: ProjectMemberRow[]
  // The signed-in user's handle — excluded from the member DM list (no self-DM).
  meHandle?: string
  // The peer handle whose DM is currently open (for active highlighting), or null.
  activePeer?: string | null
  // Which 项目文档 is open in the main area ('charter'|'decisions'|'weeklies'),
  // or null when none — so the rail can show it active.
  activeDocs?: string | null
  // Drawer width (px), made resizable by the parent.
  width?: number
  // 话题级未读 (Feishu-style): {topicId: count}; missing key = no unread.
  unreadMap?: Record<string, number>
  // 私聊未读: {peerHandle: count}, `cheese` = the 芝士 DM. Separate from
  // unreadMap because DM rows are built from the roster and have no topic id.
  privateUnreadMap?: Record<string, number>
  // 话题列表排序: the backend field/direction currently applied — the sort
  // menu just reflects and changes this, the actual ordering comes back
  // from the server in `topics` (so tree/sibling order stays consistent).
  topicSort?: TopicSortField
  topicOrder?: TopicSortOrder
}>()

const emit = defineEmits<{
  (e: 'select-topic', id: string): void
  (e: 'create-topic', title: string): void
  (e: 'split-topic', payload: { topicId: string; title: string }): void
  // 归档去向: manual archive / unarchive from the row's ⋯ actions.
  (e: 'archive-topic', id: string): void
  (e: 'unarchive-topic', id: string): void
  // Rename a topic's title from the row's ⋯ actions.
  (e: 'rename-topic', payload: { id: string; title: string }): void
  // Open the 1:1 private chat with 芝士 in the main area (飞书私聊 conversation).
  (e: 'select-private'): void
  // Open a person-to-person DM with the given member handle (飞书私聊 conversation).
  (e: 'select-peer-dm', handle: string): void
  // Open a 项目文档 (章程/决策记录/周报集) in the main area, keeping the rail.
  (e: 'select-docs', kind: 'charter' | 'decisions' | 'weeklies' | 'memory'): void
  // Live drawer width while dragging the right edge.
  (e: 'update:width', w: number): void
  // Sort menu picked a new field/direction for the topic list.
  (e: 'update:topic-sort', payload: { sort: TopicSortField; order: TopicSortOrder }): void
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

// 项目级页面（总览/日历/设置）住在项目头下（界面级合入 IA：不在顶栏）。它们和
// 这个侧栏里的其他一切一样，只换内容区——所以它们也和其他行一样显示选中态。
const router = useRouter()
const route = useRoute()
// Below this drawer width the 总览/日历/设置 labels are dropped — just the icons,
// so the row never wraps into an awkward two-line cramp on a narrow rail.
const narrowPages = computed(() => (props.width ?? 280) < 216)

// 私聊 DM list: the OTHER project members (each a person you can 1:1 DM). 芝士
// gets its own dedicated row above, and you don't DM yourself.
const peerDms = computed(() =>
  (props.members ?? [])
    .filter((m) => m.user_handle !== props.meHandle && !m.agent)
    .map((m) => ({
      handle: m.user_handle,
      name: m.name || m.user_handle,
    }))
)

const projectPages = [
  { key: 'overview', label: '总览', icon: 'mdi-view-agenda-outline' },
  { key: 'calendar', label: '日历', icon: 'mdi-calendar-outline' },
  { key: 'project-settings', label: '设置', icon: 'mdi-cog-outline' },
] as const
function openProjectPage(name: string) {
  if (!props.selectedProjectId) return
  router.push({ name, params: { projectId: props.selectedProjectId } })
}

// Project switcher dropdown. Only the caret opens it (the #activator); clicking
// the bar opens 本体. Width is captured from the 本体 box so they line up.
const bentaiMain = ref<HTMLElement | null>(null)
const switcherOpen = ref(false)
const switcherWidth = ref(248)
function captureSwitcherWidth() {
  if (bentaiMain.value) switcherWidth.value = bentaiMain.value.offsetWidth
}

// New topic: don't ask the human for a title — create an untitled one and open
// it; the title is derived from the first message (and 芝士 can refine it).
function newTopic() {
  emit('create-topic', '')
}

// ----- 话题列表排序 -----
// Four fixed combinations (field × direction) — a picker, not a builder, so a
// v-menu list beats a two-axis control for this small a option set.
const SORT_OPTIONS: Array<{ sort: TopicSortField; order: TopicSortOrder; label: string }> = [
  { sort: 'last_activity_at', order: 'desc', label: '最后活动 · 新到旧' },
  { sort: 'last_activity_at', order: 'asc', label: '最后活动 · 旧到新' },
  { sort: 'title', order: 'asc', label: '标题 · A→Z' },
  { sort: 'title', order: 'desc', label: '标题 · Z→A' },
]
const sortMenuOpen = ref(false)
const currentSortLabel = computed(
  () => SORT_OPTIONS.find((o) => o.sort === props.topicSort && o.order === props.topicOrder)?.label ?? '排序'
)
function pickSort(opt: { sort: TopicSortField; order: TopicSortOrder }) {
  sortMenuOpen.value = false
  emit('update:topic-sort', opt)
}

// ----- Topic tree -----
// A flattened tree node: a topic plus its nesting depth, so the template can
// indent without recursion. Built from the flat list via parent_id.
interface TreeRow {
  topic: Topic
  depth: number
}

function inferKind(t: Topic): string {
  // Backend may already supply `kind`; otherwise derive it from the shape:
  // a root (no parent) is 本体, a child is 分身, top-level non-root is 话题.
  const explicit = (t as Topic & { kind?: string }).kind
  if (typeof explicit === 'string' && explicit) return explicit
  if (!t.parent_id) return 'root'
  return 'subtopic'
}

const KIND_BADGE: Record<string, string> = {
  root: '全局',
  topic: '话题',
  // A task is one piece of work inside a room. It still shows in the rail for
  // now — moving it into the room's timeline as a card is a UI change of its
  // own, and dropping the row before that lands would make split-out work
  // unreachable.
  task: '任务',
  subtopic: '分身', // legacy rows, created before work had its own kind
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

  // The root topic (本体) is the rail header, not a list row — show its children
  // (work topics) at depth 0, then any other top-level topics.
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
const activeTree = computed<TreeRow[]>(() => tree.value.filter((r) => r.topic.status !== 'archived'))
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
// 私聊 badges are addressed by peer handle ('cheese' = the 芝士 DM).
function privateUnreadOf(handle: string): number {
  return props.privateUnreadMap?.[handle] ?? 0
}
// Unread hiding inside the collapsed archived group still deserves a hint.
const archivedUnread = computed<number>(() => archivedRows.value.reduce((sum, t) => sum + unreadOf(t.id), 0))

// ---- 子话题折叠 ----
// 范式跟底部的「已归档」分组一致（一个 chevron 收起一堆行），只是这里的开关
// 长在每一个有子话题的行上。行的可见性/未读聚合是纯逻辑，住在 lib/topicTree.ts
// 里（有单测），这里只管状态和落盘。
//
// 默认展开：升级前后所见完全一致，没有人会因为这次改动突然找不到自己的话题；
// "这里还有内容" 这个提示再好也弱于直接看见那一行。100+ 话题带来的长列表由
// 「收起来的状态会被记住」来解——每个人只需要把噪音大的父话题收一次。
// 按项目存 localStorage（而不是只放内存）：这个 rail 是主导航，每次刷新都要
// 重收一遍等于没有折叠。存的是**收起来的** id，所以新拆出来的话题天然可见。
const collapsedIds = ref<ReadonlySet<string>>(new Set<string>())
watch(
  () => props.selectedProjectId,
  (pid) => {
    collapsedIds.value = loadCollapsedTopics(pid)
  },
  { immediate: true }
)

// 当前选中话题的祖先链：这条路径无论祖先收没收起来都照常渲染，所以"人正待在
// 里面的那个话题"永远不会被折叠藏掉。用 reveal 而不是"自动展开"，是为了不把
// 用户自己设的折叠状态在导航时偷偷改写——离开之后那一支照旧是收起来的。
const selectedPath = computed(() => ancestorPathIds(props.topics, props.selectedTopicId))
const visibleTree = computed(() =>
  visibleRows(activeTree.value, {
    collapsed: collapsedIds.value,
    reveal: selectedPath.value,
    unreadOf,
  })
)

function toggleCollapse(id: string) {
  const next = new Set(collapsedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsedIds.value = next
  saveCollapsedTopics(props.selectedProjectId, next)
}

// The root topic (本体) — represented by the rail header (a selector + a click
// target), not a list row. And the current project's display name.
const rootTopic = computed<Topic | null>(() => props.topics.find((t) => inferKind(t) === 'root') ?? null)
const currentProjectName = computed<string>(
  () => props.projects.find((p) => p.id === props.selectedProjectId)?.name ?? '选择项目'
)

// Inline rename (pattern mirrors MyDevicesView's rename-in-place): a click on
// the pencil swaps the title span for a text field; enter/blur commits.
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

function onSplit(t: Topic) {
  // Never ask the human for a title (spec §rule 4, mirrors newTopic()). The
  // sub-topic is born untitled and opened; its title is derived from the first
  // message (芝士 can refine it via a tool).
  emit('split-topic', { topicId: t.id, title: '' })
}

// 项目文档 (spec §7.1): 章程 / 决策记录 / 周报集 open INSIDE the 工作台 (keeping
// the left rail), via the docs mode — not a separate full-screen route. Active
// state comes from the parent's current docs kind.
const onCharter = computed(() => props.activeDocs === 'charter')
const onDecisions = computed(() => props.activeDocs === 'decisions')
const onWeeklies = computed(() => props.activeDocs === 'weeklies')
const onMemory = computed(() => props.activeDocs === 'memory')
</script>

<template>
  <v-navigation-drawer permanent :width="width ?? 280" color="surface" border="e">
    <!-- Drag handle on the right edge to resize the rail. -->
    <div class="rail-resizer" title="拖动调整宽度" @mousedown="startResize" />
    <div class="d-flex flex-column fill-height">
      <!-- 本体 = 项目 = 根话题: one flush header that opens the 本体 (root topic)
           on click, and switches projects via the caret menu. -->
      <div class="bentai-bar" :class="{ 'is-active': !!rootTopic && rootTopic.id === selectedTopicId }">
        <button
          ref="bentaiMain"
          type="button"
          class="bentai-bar__main"
          @click="rootTopic && emit('select-topic', rootTopic.id)"
        >
          <v-icon size="18" class="bentai-bar__icon">mdi-hexagon-outline</v-icon>
          <span class="bentai-bar__name">{{ currentProjectName }}</span>
          <span class="chip-neutral">全局</span>
          <span v-if="rootTopic && unreadOf(rootTopic.id) > 0" class="unread-badge">{{
            unreadLabel(rootTopic.id)
          }}</span>
        </button>
        <!-- 切换项目已回归左侧 rail（每个项目一个图标）——此处不再放切换器。 -->
      </div>

      <!-- 项目级页面（总览/日历/设置）: Slack 式置顶行，属项目上下文而非顶栏。 -->
      <div v-if="selectedProjectId" class="proj-pages" :class="{ 'proj-pages--compact': narrowPages }">
        <button
          v-for="p in projectPages"
          :key="p.key"
          type="button"
          class="proj-pages__item"
          :class="{ 'is-active': route.name === p.key }"
          :title="p.label"
          @click="openProjectPage(p.key)"
        >
          <v-icon size="15">{{ p.icon }}</v-icon>
          <span v-if="!narrowPages">{{ p.label }}</span>
        </button>
      </div>

      <v-divider />

      <!-- Scrollable lists -->
      <div class="flex-grow-1 overflow-y-auto">
        <template v-if="!selectedProjectId">
          <div class="t-body c-muted pa-4">先选择一个项目</div>
        </template>
        <template v-else>
          <div class="t-eyebrow side-subhead side-subhead--row">
            <span>话题</span>
            <div class="d-flex align-center ga-1">
              <v-menu v-model="sortMenuOpen" location="bottom end">
                <template #activator="{ props: menuProps }">
                  <v-btn
                    v-bind="menuProps"
                    icon="mdi-sort"
                    size="x-small"
                    variant="text"
                    :title="`排序：${currentSortLabel}`"
                  />
                </template>
                <v-list density="compact" nav>
                  <v-list-item
                    v-for="opt in SORT_OPTIONS"
                    :key="`${opt.sort}-${opt.order}`"
                    :active="opt.sort === topicSort && opt.order === topicOrder"
                    @click="pickSort(opt)"
                  >
                    <v-list-item-title class="t-body">{{ opt.label }}</v-list-item-title>
                  </v-list-item>
                </v-list>
              </v-menu>
              <v-btn
                icon="mdi-plus"
                size="x-small"
                variant="tonal"
                color="primary"
                title="新建话题"
                @click="newTopic"
              />
            </div>
          </div>

          <div v-if="loadingTopics" class="px-4 py-2">
            <v-progress-circular indeterminate size="20" width="2" color="primary" />
          </div>

          <v-list v-else density="compact" nav class="py-0">
            <v-list-item
              v-for="row in visibleTree"
              :key="row.topic.id"
              :active="row.topic.id === selectedTopicId"
              rounded="lg"
              class="topic-row"
              :class="{
                'is-active': row.topic.id === selectedTopicId,
                'is-sub': row.depth > 0,
              }"
              :style="{
                paddingInlineStart: 8 + row.depth * 20 + 'px',
                '--guide-x': 16 + (row.depth - 1) * 20 + 'px',
              }"
              @click="emit('select-topic', row.topic.id)"
            >
              <!-- 干净行 + 前置图标做身份锚（混合版）：图标未读变琥珀，
                   种类标签仍不要（缩进表达层级），操作 hover 才浮现。 -->
              <template #prepend>
                <!-- 折叠开关：只有真有子话题的行才画，没有的行留同宽占位，
                     免得两种行的图标错开一列。 -->
                <button
                  v-if="row.hasChildren"
                  type="button"
                  class="subtree-toggle"
                  :title="row.collapsed ? '展开子话题' : '收起子话题'"
                  :aria-expanded="!row.collapsed"
                  @click.stop="toggleCollapse(row.topic.id)"
                >
                  <v-icon size="15" class="c-faint">
                    {{ row.collapsed ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
                  </v-icon>
                </button>
                <span v-else class="subtree-toggle subtree-toggle--empty" />
                <span class="row-glyph-wrap">
                  <v-icon
                    v-if="row.depth === 0"
                    size="16"
                    class="row-glyph"
                    :class="{ 'row-glyph--unread': row.unreadTotal > 0 }"
                    icon="mdi-message-text-outline"
                  />
                  <!-- 分身不用钩子箭头：树的结构交给缩进 + 竖向引导线，
                       行内只留一个小圆点做锚（未读转琥珀）。 -->
                  <span v-else class="row-glyph row-glyph--dot" :class="{ 'row-glyph--unread': row.unreadTotal > 0 }" />
                  <!-- 芝士还在这个话题里跑这一轮：呼吸点，人凭它判断啥时候
                       该派下一个任务——和归档/采纳状态无关，只是本轮有没有跑完。 -->
                  <span v-if="row.topic.running" class="running-dot" title="芝士正在这个话题里工作" />
                </span>
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
                  <span class="text-truncate" :class="{ 'title-unread': row.unreadTotal > 0 }">{{
                    row.topic.title
                  }}</span>
                  <!-- 收起来了就说清楚收了多少——「这里还有内容」得看得见。 -->
                  <span
                    v-if="row.collapsed && row.hiddenCount > 0"
                    class="subtree-count ms-2"
                    :title="`收起了 ${row.hiddenCount} 个子话题`"
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
                <!-- items 感的右锚：没未读时给最后活跃时间（真实信息，非装饰）。
                     updated_at 是兜底：它只在话题行自己被改过时才动，回答不了
                     "最后有动静是什么时候"，只用在 last_activity_at 缺席的接口
                     返回上（新建/改名/归档的响应体）。 -->
                <span v-else class="row-time">{{ relTime(row.topic.last_activity_at ?? row.topic.updated_at) }}</span>
                <!-- hover 浮出的操作层：绝对定位覆盖行尾，不占布局宽度 -->
                <div class="row-actions" @click.stop>
                  <v-btn
                    icon="mdi-pencil-outline"
                    size="small"
                    variant="text"
                    density="comfortable"
                    title="重命名"
                    @click.stop="startRename(row.topic)"
                  />
                  <v-btn
                    icon="mdi-archive-arrow-down-outline"
                    size="small"
                    variant="text"
                    density="comfortable"
                    title="归档话题"
                    @click.stop="emit('archive-topic', row.topic.id)"
                  />
                  <v-btn
                    icon="mdi-source-branch-plus"
                    size="small"
                    variant="text"
                    density="comfortable"
                    title="拆出子话题"
                    @click.stop="onSplit(row.topic)"
                  />
                </div>
              </template>
            </v-list-item>

            <v-list-item v-if="activeTree.length === 0" class="c-faint t-body"> 暂无话题 </v-list-item>
          </v-list>

          <!-- 归档去向: collapsed 已归档 group at the bottom of the topic list.
               Archived topics leave the active tree and land here (newest
               first), so done work stops crowding the rail. -->
          <template v-if="archivedRows.length">
            <button type="button" class="archived-toggle" @click="archivedOpen = !archivedOpen">
              <v-icon size="15" class="c-faint">
                {{ archivedOpen ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
              </v-icon>
              <span class="t-eyebrow">已归档</span>
              <span class="archived-count">{{ archivedRows.length }}</span>
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

          <!-- 项目文档 -->
          <div class="t-eyebrow side-subhead">项目文档</div>
          <v-list density="compact" nav class="py-0">
            <v-list-item
              :active="onCharter"
              :disabled="!selectedProjectId"
              rounded="lg"
              class="nav-row"
              :class="{ 'is-active': onCharter }"
              prepend-icon="mdi-file-document-outline"
              title="章程"
              @click="emit('select-docs', 'charter')"
            />
            <v-list-item
              :active="onDecisions"
              :disabled="!selectedProjectId"
              rounded="lg"
              class="nav-row"
              :class="{ 'is-active': onDecisions }"
              prepend-icon="mdi-clipboard-text-clock-outline"
              title="决策记录"
              @click="emit('select-docs', 'decisions')"
            />
            <v-list-item
              :active="onWeeklies"
              :disabled="!selectedProjectId"
              rounded="lg"
              class="nav-row"
              :class="{ 'is-active': onWeeklies }"
              prepend-icon="mdi-calendar-week-outline"
              title="周报集"
              @click="emit('select-docs', 'weeklies')"
            />
            <v-list-item
              :active="onMemory"
              :disabled="!selectedProjectId"
              rounded="lg"
              class="nav-row"
              :class="{ 'is-active': onMemory }"
              prepend-icon="mdi-brain"
              title="记忆"
              @click="emit('select-docs', 'memory')"
            />
          </v-list>

          <v-divider class="mx-3 my-1" />

          <!-- 私聊 (飞书私聊): 1:1 conversations — 芝士 plus each other project
               member — each opens as a normal chat in the main area. -->
          <div class="t-eyebrow side-subhead">私聊</div>
          <v-list density="compact" nav class="py-0">
            <v-list-item
              :active="privateActive"
              rounded="lg"
              class="nav-row private-row"
              :class="{ 'is-active': privateActive }"
              @click="emit('select-private')"
            >
              <template #prepend>
                <!-- 芝士头像放进与图标同宽 (16px) 的定宽槽并居中：头像 18px，
                     视觉上与话题/项目文档那一列的 ~16px 图标同大，icon-left 与
                     text-left 都能和那一列对齐。 -->
                <span class="private-avatar-slot">
                  <CheeseAvatar :size="18" />
                </span>
              </template>
              <v-list-item-title class="t-body" style="font-weight: 500; color: var(--ink)"> 芝士 </v-list-item-title>
              <template #append>
                <span v-if="privateUnreadOf('cheese') > 0" class="unread-badge">
                  {{ countLabel(privateUnreadOf('cheese')) }}
                </span>
              </template>
            </v-list-item>

            <!-- Person-to-person DMs: one row per OTHER project member. -->
            <v-list-item
              v-for="dm in peerDms"
              :key="dm.handle"
              :active="activePeer === dm.handle"
              rounded="lg"
              class="nav-row private-row"
              :class="{ 'is-active': activePeer === dm.handle }"
              @click="emit('select-peer-dm', dm.handle)"
            >
              <template #prepend>
                <span class="private-avatar-slot">
                  <span class="dm-avatar" :style="{ backgroundColor: avatarColor(dm.handle) }">{{
                    dm.name.slice(0, 1).toUpperCase()
                  }}</span>
                </span>
              </template>
              <v-list-item-title class="t-body" style="font-weight: 500; color: var(--ink)">
                {{ dm.name }}
              </v-list-item-title>
              <template #append>
                <span v-if="privateUnreadOf(dm.handle) > 0" class="unread-badge">
                  {{ countLabel(privateUnreadOf(dm.handle)) }}
                </span>
              </template>
            </v-list-item>
          </v-list>
        </template>
      </div>

      <!-- 新建项目 moved to the project rail's + (App.vue) — one affordance,
           Discord-style. The create-project emit stays for API compatibility. -->
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
/* Right-edge resize handle (sits on top of the drawer's border). */
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

/* 本体 = 项目 header row: flush-left (the topic tree's parent, not deeper). */
.bentai-bar {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 6px 6px 6px 10px;
}

/* 项目级页面行 (总览/日历/设置) — quiet, Slack-pinned-row feel. */
.proj-pages {
  display: flex;
  gap: 4px;
  padding: 0 10px 8px;
}
.proj-pages__item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  transition:
    background 120ms ease,
    color 120ms ease;
}
.proj-pages__item:hover {
  background: var(--fill-2);
  color: var(--ink);
}
.proj-pages__item.is-active {
  background: var(--fill-2);
  color: var(--ink);
  font-weight: 600;
}
/* narrow rail: icon-only, evenly spread, no label wrap */
.proj-pages--compact .proj-pages__item {
  flex: 1;
  justify-content: center;
  padding: 6px 0;
}
.bentai-bar__main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 6px;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  transition: background 0.12s ease;
}
.bentai-bar__main:hover {
  background: var(--fill);
}
.bentai-bar.is-active .bentai-bar__main {
  background: var(--fill);
}
.bentai-bar__icon {
  flex: none;
  color: var(--muted);
}
.bentai-bar.is-active .bentai-bar__icon {
  color: var(--accent);
}
.bentai-bar__name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
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

/* Active row: --fill bg + 2px --accent left bar + --ink text. NOT a tinted
   amber fill. Kill Vuetify's default active overlay so no amber bleeds in. */
.topic-row.is-active,
.nav-row.is-active {
  background: var(--fill);
}
.topic-row.is-active :deep(.v-list-item__overlay),
.nav-row.is-active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}
.topic-row.is-active::before,
.nav-row.is-active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 2px;
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  background: var(--accent);
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

/* 已归档 group toggle at the bottom of the topic list. */
.archived-toggle {
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
.archived-toggle:hover {
  background: var(--fill);
}
.archived-toggle .t-eyebrow {
  padding: 0;
}
.archived-count {
  font-size: 11px;
  color: var(--faint);
  background: var(--fill);
  border-radius: 8px;
  padding: 1px 6px;
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
   nav-row (项目文档/私聊) 必须共用同一套：否则图标虽同列，文字却各自缩进
   （话题 24px、项目文档 56px、私聊 32px），三列文字对不齐。 */
.topic-row :deep(.v-list-item__spacer),
.nav-row :deep(.v-list-item__spacer) {
  width: 8px !important;
}
/* 图标槽统一成 16px 定宽方块：话题用 size=16 的 v-icon，项目文档用
   prepend-icon（默认 24），私聊用 24px 头像。锁死 prepend 里图标的字号与
   槽宽，icon-left 与 text-left 才能双双成列。 */
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
/* 私聊行的头像槽：定宽 16px、与图标同列；18px 头像在其中居中、略微溢出，
   视觉大小与话题/项目文档那一列的图标持平，头像左缘落在同一图标列、
   文字左缘落在同一文字列。 */
.private-avatar-slot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex: none;
  overflow: visible;
}
/* Human DM avatar: an initial in a muted circle, sized to match 芝士's 18px
   avatar so both DM columns share the same icon/text lead. */
.dm-avatar {
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
/* Item 感（混合版）：前置图标做行的身份锚，未读转琥珀。 */
.row-glyph {
  color: var(--faint);
}
.row-glyph--dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  margin-left: 5px;
  flex: none;
}
.row-glyph--unread {
  color: var(--accent);
}
.row-glyph-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

/* 子话题折叠开关：定宽槽，没有子话题的行放同宽占位，图标列才不会错开。 */
.subtree-toggle {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  margin-right: 2px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.subtree-toggle:hover {
  background: var(--fill);
}
.subtree-toggle--empty {
  cursor: default;
  pointer-events: none;
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
/* 呼吸点：芝士还在这一轮里工作，跟归档/采纳状态无关。 */
.running-dot {
  position: absolute;
  right: -3px;
  bottom: -3px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 0 1.5px var(--surface);
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
/* 分身组的竖向引导线：把一串子话题挂在父话题下（Linear/Notion 树形手法）。 */
.topic-row.is-sub::before {
  content: '';
  position: absolute;
  left: var(--guide-x, 24px);
  top: -3px;
  bottom: -3px;
  width: 1px;
  background: var(--line-2);
}
.topic-row:hover {
  background: var(--fill);
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
/* 工具条里的每颗按钮要有自己的悬停反馈——否则不像能按的东西。
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
/* 取消归档按钮与归档/拆分同属一个按钮家族：同样的 25px 方盒、7px 圆角、
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
.topic-row:hover .row-actions,
.topic-row:focus-within .row-actions {
  opacity: 1;
  pointer-events: auto;
}
/* While the actions are out, the count steps aside (they share the tail). */
.topic-row:hover .unread-badge,
.topic-row:focus-within .unread-badge,
.topic-row:hover .row-time,
.topic-row:focus-within .row-time {
  opacity: 0;
}
.row-time {
  flex: none;
  color: var(--faint);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  /* 纯展示元素：绝不吃鼠标——hover 时它只是隐形，曾把整个操作工具条挡成
     "点不动"（playwright 抓的现行：row-time intercepts pointer events）。 */
  pointer-events: none;
}
</style>

<!-- Non-scoped: the project switcher renders in a teleported v-menu overlay,
     so scoped styles wouldn't reach it. Tokens are global (:root). -->
<style>
.proj-switcher {
  min-width: 248px;
  padding: 6px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 12px;
  box-shadow: var(--shadow-2);
}
.proj-switcher__head {
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--faint);
  padding: 6px 10px 4px;
}
.proj-switcher__row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border-radius: 8px;
  text-align: left;
  cursor: pointer;
  color: var(--text);
  transition: background 0.12s ease;
}
.proj-switcher__row:hover {
  background: var(--fill);
}
.proj-switcher__row.is-active {
  background: var(--accent-wash);
}
.proj-switcher__icon {
  flex: none;
  color: var(--muted);
}
.proj-switcher__row.is-active .proj-switcher__icon {
  color: var(--accent);
}
.proj-switcher__name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.proj-switcher__check {
  flex: none;
  color: var(--accent);
}
</style>
