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
}>()

const emit = defineEmits<{
  (e: 'select-project', id: string): void
  (e: 'select-topic', id: string): void
  (e: 'create-project', name: string): void
  (e: 'create-topic', title: string): void
  (e: 'split-topic', payload: { topicId: string; title: string }): void
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
const newTopicTitle = ref('')

function submitProject() {
  const name = newProjectName.value.trim()
  if (!name) return
  emit('create-project', name)
  newProjectName.value = ''
}

function submitTopic() {
  const title = newTopicTitle.value.trim()
  if (!title) return
  emit('create-topic', title)
  newTopicTitle.value = ''
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

  // Roots first: the root topic (本体) before other top-level topics. A
  // top-level topic is one whose parent_id is null or points outside the list.
  const idSet = new Set(all.map((t) => t.id))
  const roots = all.filter((t) => !t.parent_id || !idSet.has(t.parent_id))
  roots.sort((a, b) => {
    const ar = inferKind(a) === 'root' ? 0 : 1
    const br = inferKind(b) === 'root' ? 0 : 1
    if (ar !== br) return ar - br
    return a.created_at.localeCompare(b.created_at)
  })
  for (const r of roots) visit(r, 0)

  // Safety: append any orphans not reached (cycles / dangling parents).
  for (const t of all) if (!seen.has(t.id)) rows.push({ topic: t, depth: 0 })

  return rows
})

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
      <!-- (Project switching lives in the global top-bar picker — no duplicate
           here.) The rail is the current project's topic tree + 新建项目. -->

      <!-- Scrollable lists -->
      <div class="flex-grow-1 overflow-y-auto">
        <template v-if="!selectedProjectId">
          <div class="t-body c-muted pa-4">先选择一个项目</div>
        </template>
        <template v-else>
          <div class="t-eyebrow side-subhead">话题</div>

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
              v-for="row in tree"
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
                <span class="text-truncate">{{ row.topic.title }}</span>
                <span class="kind-text">{{ kindLabel(row.topic) }}</span>
                <span
                  v-if="statusBadge(row.topic.status)"
                  class="d-inline-flex align-center ga-1 c-faint topic-status"
                >
                  <span
                    class="status-dot"
                    :class="
                      row.topic.status === 'archived'
                        ? 'status-dot--muted'
                        : 'status-dot--warn'
                    "
                  />
                  {{ statusBadge(row.topic.status) }}
                </span>
              </v-list-item-title>
              <template #append>
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

            <v-list-item v-if="tree.length === 0" class="c-faint t-body">
              暂无话题
            </v-list-item>
          </v-list>

          <!-- New topic -->
          <div class="px-3 pb-2 pt-1">
            <v-text-field
              v-model="newTopicTitle"
              placeholder="新建话题…"
              density="compact"
              variant="outlined"
              hide-details
              append-inner-icon="mdi-plus"
              @click:append-inner="submitTopic"
              @keydown.enter="submitTopic"
            />
          </div>

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
  right: 0;
  bottom: 0;
  width: 5px;
  cursor: col-resize;
  z-index: 3;
  transition: background 0.12s ease;
}
.rail-resizer:hover {
  background: var(--accent);
}
.side-subhead {
  padding: 14px 16px 4px;
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
