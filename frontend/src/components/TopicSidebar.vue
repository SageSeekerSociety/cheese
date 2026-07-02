<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Project, Topic } from '../types'
import CheeseAvatar from './CheeseAvatar.vue'

const props = defineProps<{
  projects: Project[]
  selectedProjectId: string | null
  topics: Topic[]
  selectedTopicId: string | null
  loadingTopics: boolean
  // True when the 私聊 (1:1 with 芝士) entry is the active main view.
  privateActive: boolean
  // Which 项目文档 is open in the main area ('charter'|'decisions'|'weeklies'),
  // or null when none — so the rail can show it active.
  activeDocs?: string | null
  // Drawer width (px), made resizable by the parent.
  width?: number
  // 话题级未读 (Feishu-style): {topicId: count}; missing key = no unread.
  unreadMap?: Record<string, number>
}>()

const emit = defineEmits<{
  (e: 'select-project', id: string): void
  (e: 'select-topic', id: string): void
  (e: 'create-project', name: string): void
  (e: 'create-topic', title: string): void
  (e: 'split-topic', payload: { topicId: string; title: string }): void
  // 归档去向: manual archive / unarchive from the row's ⋯ actions.
  (e: 'archive-topic', id: string): void
  (e: 'unarchive-topic', id: string): void
  // Open the 1:1 private chat with 芝士 in the main area (飞书私聊 conversation).
  (e: 'select-private'): void
  // Open a 项目文档 (章程/决策记录/周报集) in the main area, keeping the rail.
  (e: 'select-docs', kind: 'charter' | 'decisions' | 'weeklies'): void
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

const newProjectName = ref('')

// Project switcher dropdown. Only the caret opens it (the #activator); clicking
// the bar opens 本体. Width is captured from the 本体 box so they line up.
const bentaiMain = ref<HTMLElement | null>(null)
const switcherOpen = ref(false)
const switcherWidth = ref(248)
function captureSwitcherWidth() {
  if (bentaiMain.value) switcherWidth.value = bentaiMain.value.offsetWidth
}

function submitProject() {
  const name = newProjectName.value.trim()
  if (!name) return
  emit('create-project', name)
  newProjectName.value = ''
}

// New topic: don't ask the human for a title — create an untitled one and open
// it; the title is derived from the first message (and 芝士 can refine it).
function newTopic() {
  emit('create-topic', '')
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
  root: '本体',
  topic: '话题',
  subtopic: '分身',
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
const activeTree = computed<TreeRow[]>(() =>
  tree.value.filter((r) => r.topic.status !== 'archived'),
)
const archivedRows = computed<Topic[]>(() =>
  props.topics
    .filter((t) => t.status === 'archived' && inferKind(t) !== 'root')
    .sort((a, b) => (b.archived_at ?? '').localeCompare(a.archived_at ?? '')),
)
const archivedOpen = ref(false)

// ---- 话题级未读角标 (Feishu-style) ----
function unreadOf(id: string): number {
  return props.unreadMap?.[id] ?? 0
}
// The badge shows at most 99+ (a runaway count shouldn't stretch the row).
function unreadLabel(id: string): string {
  const n = unreadOf(id)
  return n > 99 ? '99+' : String(n)
}
// Unread hiding inside the collapsed archived group still deserves a hint.
const archivedUnread = computed<number>(() =>
  archivedRows.value.reduce((sum, t) => sum + unreadOf(t.id), 0),
)

// The root topic (本体) — represented by the rail header (a selector + a click
// target), not a list row. And the current project's display name.
const rootTopic = computed<Topic | null>(
  () => props.topics.find((t) => inferKind(t) === 'root') ?? null,
)
const currentProjectName = computed<string>(
  () =>
    props.projects.find((p) => p.id === props.selectedProjectId)?.name ??
    '选择项目',
)

function onSplit(t: Topic) {
  const title = window.prompt(`在「${t.title}」下新建子话题，标题：`)
  if (!title || !title.trim()) return
  emit('split-topic', { topicId: t.id, title: title.trim() })
}

// 项目文档 (spec §7.1): 章程 / 决策记录 / 周报集 open INSIDE the 工作台 (keeping
// the left rail), via the docs mode — not a separate full-screen route. Active
// state comes from the parent's current docs kind.
const onCharter = computed(() => props.activeDocs === 'charter')
const onDecisions = computed(() => props.activeDocs === 'decisions')
const onWeeklies = computed(() => props.activeDocs === 'weeklies')
</script>

<template>
  <v-navigation-drawer permanent :width="width ?? 280" color="surface" border="e">
    <!-- Drag handle on the right edge to resize the rail. -->
    <div class="rail-resizer" title="拖动调整宽度" @mousedown="startResize" />
    <div class="d-flex flex-column fill-height">
      <!-- 本体 = 项目 = 根话题: one flush header that opens the 本体 (root topic)
           on click, and switches projects via the caret menu. -->
      <div
        class="bentai-bar"
        :class="{ 'is-active': !!rootTopic && rootTopic.id === selectedTopicId }"
      >
        <button
          ref="bentaiMain"
          type="button"
          class="bentai-bar__main"
          @click="rootTopic && emit('select-topic', rootTopic.id)"
        >
          <v-icon size="18" class="bentai-bar__icon">mdi-hexagon-outline</v-icon>
          <span class="bentai-bar__name">{{ currentProjectName }}</span>
          <span class="chip-neutral">本体</span>
          <span
            v-if="rootTopic && unreadOf(rootTopic.id) > 0"
            class="unread-badge"
          >{{ unreadLabel(rootTopic.id) }}</span>
        </button>
        <!-- ONLY the caret opens the switcher (clicking the bar opens 本体). -->
        <v-menu v-model="switcherOpen" location="bottom end" :offset="6">
          <template #activator="{ props: mp }">
            <v-btn
              v-bind="mp"
              icon="mdi-unfold-more-horizontal"
              size="x-small"
              variant="text"
              title="切换项目"
              @click.stop="captureSwitcherWidth"
            />
          </template>
          <div class="proj-switcher" :style="{ width: switcherWidth + 'px' }">
            <div class="proj-switcher__head">切换项目</div>
            <button
              v-for="p in projects"
              :key="p.id"
              type="button"
              class="proj-switcher__row"
              :class="{ 'is-active': p.id === selectedProjectId }"
              @click="emit('select-project', p.id)"
            >
              <v-icon size="17" class="proj-switcher__icon"
                >mdi-hexagon-outline</v-icon
              >
              <span class="proj-switcher__name">{{ p.name }}</span>
              <v-icon
                v-if="p.id === selectedProjectId"
                size="15"
                class="proj-switcher__check"
                >mdi-check</v-icon
              >
            </button>
          </div>
        </v-menu>
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
            <v-btn
              icon="mdi-plus"
              size="x-small"
              variant="tonal"
              color="primary"
              title="新建话题"
              @click="newTopic"
            />
          </div>

          <div v-if="loadingTopics" class="px-4 py-2">
            <v-progress-circular
              indeterminate
              size="20"
              width="2"
              color="primary"
            />
          </div>

          <v-list v-else density="compact" nav class="py-0">
            <v-list-item
              v-for="row in activeTree"
              :key="row.topic.id"
              :active="row.topic.id === selectedTopicId"
              rounded="lg"
              class="topic-row"
              :class="{ 'is-active': row.topic.id === selectedTopicId }"
              :style="{ paddingInlineStart: 12 + row.depth * 16 + 'px' }"
              @click="emit('select-topic', row.topic.id)"
            >
              <template #prepend>
                <v-icon
                  size="17"
                  class="me-1 c-faint"
                  :icon="
                    row.depth > 0
                      ? 'mdi-subdirectory-arrow-right'
                      : 'mdi-message-text-outline'
                  "
                />
              </template>
              <v-list-item-title class="d-flex align-center ga-2 topic-title">
                <span
                  class="text-truncate"
                  :class="{ 'title-unread': unreadOf(row.topic.id) > 0 }"
                >{{ row.topic.title }}</span>
                <span class="kind-text">{{ kindLabel(row.topic) }}</span>
                <span
                  v-if="statusBadge(row.topic.status)"
                  class="d-inline-flex align-center ga-1 c-faint topic-status"
                >
                  <span class="status-dot status-dot--warn" />
                  {{ statusBadge(row.topic.status) }}
                </span>
              </v-list-item-title>
              <template #append>
                <!-- 未读：标题加粗是信号，计数只是裸的灰色小数字——
                     每行一颗实心药丸（无论什么色）都是视觉铆钉。 -->
                <span
                  v-if="unreadOf(row.topic.id) > 0"
                  class="unread-badge me-1"
                >{{ unreadLabel(row.topic.id) }}</span>
                <v-btn
                  icon="mdi-archive-arrow-down-outline"
                  size="x-small"
                  variant="text"
                  density="comfortable"
                  title="归档话题"
                  class="split-btn"
                  @click.stop="emit('archive-topic', row.topic.id)"
                />
                <v-btn
                  icon="mdi-source-branch-plus"
                  size="x-small"
                  variant="text"
                  density="comfortable"
                  title="拆出子话题"
                  class="split-btn"
                  @click.stop="onSplit(row.topic)"
                />
              </template>
            </v-list-item>

            <v-list-item v-if="activeTree.length === 0" class="c-faint t-body">
              暂无话题
            </v-list-item>
          </v-list>

          <!-- 归档去向: collapsed 已归档 group at the bottom of the topic list.
               Archived topics leave the active tree and land here (newest
               first), so done work stops crowding the rail. -->
          <template v-if="archivedRows.length">
            <button
              type="button"
              class="archived-toggle"
              @click="archivedOpen = !archivedOpen"
            >
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
                    size="x-small"
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
          </v-list>

          <v-divider class="mx-3 my-1" />

          <!-- 私聊 (飞书私聊): a 1:1 conversation with 芝士, opens in main area -->
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
                <CheeseAvatar :size="24" class="me-2" />
              </template>
              <v-list-item-title class="t-body" style="font-weight: 500; color: var(--ink)">
                与芝士私聊
              </v-list-item-title>
              <v-list-item-subtitle class="t-meta">
                只有你能看到
              </v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </template>
      </div>

      <v-divider />

      <!-- New project footer -->
      <div class="pa-3">
        <v-text-field
          v-model="newProjectName"
          placeholder="新建项目…"
          density="compact"
          variant="outlined"
          hide-details
          append-inner-icon="mdi-plus"
          @click:append-inner="submitProject"
          @keydown.enter="submitProject"
        />
      </div>
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
  border-radius: 0 2px 2px 0;
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 未读计数 = 裸数字，无底色：信号在标题加粗上，数字只是补充信息。
     （先红圆后石墨药丸都被否了——形态才是问题，review 2026-07-03） */
  background: none;
  color: var(--muted, #8a8a8a);
  margin-left: 6px;
  font-size: 10.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}
/* Collapsed 已归档 header: just a dot hint, not a count. */
.unread-badge--dot {
  width: 6px;
  height: 6px;
  padding: 0;
  border-radius: 50%;
  background: var(--muted, #8a8a8a);
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

/* Row split button stays a stable, always-clickable target. It was previously
   opacity:0 until the whole row was hovered, which made it "run away" — moving
   the cursor toward it near the row edge flickered the hover state and the
   button vanished. Keep it faint-but-present, brightening on hover/focus. */
.topic-row .split-btn {
  opacity: 0.4;
  color: var(--faint);
  transition: opacity 0.12s ease, color 0.12s ease;
}
.topic-row:hover .split-btn,
.topic-row:focus-within .split-btn,
.topic-row .split-btn:hover,
.topic-row .split-btn:focus-visible {
  opacity: 1;
  color: var(--muted);
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
  box-shadow: 0 12px 32px rgba(20, 22, 26, 0.14);
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
