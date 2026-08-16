<script setup lang="ts">
import type { MemoryEntryOut } from '../api'
import type { Block, Topic } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

import { deleteMemory, getProject, getProjectDecisions, listMemory, listTopics } from '../api'
import DocEditor from '../components/DocEditor.vue'
import { relTime } from '../lib/relTime'
import { myHandle } from '../me'

// 项目级文档 (spec §7.1): 章程 / 决策记录 / 周报集. Shown either as a standalone
// route or embedded inside the 工作台 (keeping the left rail) — `kind`/`embedded`
// override the route when embedded.
type Kind = 'charter' | 'decisions' | 'weeklies' | 'memory'
const props = defineProps<{
  projectId: string
  kind?: Kind
  embedded?: boolean
}>()
const route = useRoute()

const AUTHOR = myHandle()

// Which document to show: an explicit prop (embedded) wins over the route name.
const kind = computed<Kind>(() => {
  if (props.kind) return props.kind
  if (route.name === 'project-decisions') return 'decisions'
  if (route.name === 'project-weeklies') return 'weeklies'
  return 'charter'
})

const TITLES: Record<Kind, string> = {
  charter: '章程',
  decisions: '决策记录',
  weeklies: '周报集',
  memory: '记忆',
}
const OVERLINES: Record<Kind, string> = {
  charter: '项目文档',
  decisions: '项目文档',
  weeklies: '项目文档',
  memory: '芝士记住的事',
}

const projectName = ref<string>('')
const loading = ref(false)
const error = ref<string | null>(null)

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }) as string)
}

// ---- 章程: the root topic's living doc (改了就等于给芝士下指令). The rich
// editor (DocEditor) owns loading/saving the doc's markdown via the same
// getDoc/putDoc API DocPanel uses. Like the workspace DocPanel, it is ALWAYS
// editable and autosaves (debounce + ⌘S + blur) — no 编辑 toggle. Here we only
// mirror the save-status indicator it emits. ----
const rootTopicId = ref<string | null>(null)
const saving = ref(false)
const savedAt = ref<number | null>(null)
const charterDirty = ref(false)

function onCharterSaving() {
  saving.value = true
  savedAt.value = null
}
function onCharterSaved() {
  saving.value = false
  charterDirty.value = false
  savedAt.value = Date.now()
}
function onCharterDirty() {
  charterDirty.value = true
  savedAt.value = null
}
function onCharterError(message: string) {
  saving.value = false
  error.value = message
}

// ---- 决策记录 ----
const decisions = ref<Block[]>([])

// ---- 周报集: topics whose title contains 周报 ----
const weeklies = ref<Topic[]>([])

function fmtDate(d: string | null): string {
  if (!d) return ''
  return d.length >= 10 ? d.slice(0, 10) : d
}

// ---- 记忆 (spec §8.4 记忆可见): entries 芝士 remembered, human-prunable ----
const memoryEntries = ref<MemoryEntryOut[]>([])
async function loadMemory() {
  const payload = await listMemory(props.projectId, AUTHOR)
  memoryEntries.value = payload.data
}
async function removeMemory(id: string) {
  await deleteMemory(id)
  memoryEntries.value = memoryEntries.value.filter((e) => e.id !== id)
}

async function load() {
  loading.value = true
  error.value = null
  savedAt.value = null
  try {
    const project = await getProject(props.projectId)
    projectName.value = project.name

    if (kind.value === 'charter') {
      // DocEditor loads/persists the doc itself once rootTopicId is set.
      rootTopicId.value = project.root_topic_id ?? null
      charterDirty.value = false
    } else if (kind.value === 'decisions') {
      const payload = await getProjectDecisions(props.projectId)
      decisions.value = payload.data
    } else if (kind.value === 'memory') {
      await loadMemory()
    } else {
      const payload = await listTopics(props.projectId)
      weeklies.value = payload.data.filter((t) => t.title.includes('周报'))
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

// 返回工作台: project workspace (defaults to the root topic).
const workspaceTo = { name: 'workspace-project', params: { projectId: props.projectId } }

// A source-topic link: open the workspace AND pre-select that topic via ?topic=.
// Without the query, WorkspaceView always falls back to the root topic.
function topicTo(topicId: string | null | undefined) {
  if (!topicId) return workspaceTo
  return {
    name: 'workspace-project',
    params: { projectId: props.projectId },
    query: { topic: topicId },
  }
}

watch(() => [props.projectId, route.name], load)
onMounted(load)
// Embedded in the workspace the component persists across 章程/决策/周报/记忆
// switches — each switch must refetch or the new page shows stale/empty data.
watch([kind, () => props.projectId], load)
</script>

<template>
  <div class="docs-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 920px">
      <!-- Header (the back link is redundant when embedded — the rail is there) -->
      <v-btn
        v-if="!props.embedded"
        :to="workspaceTo"
        variant="text"
        size="small"
        prepend-icon="mdi-arrow-left"
        class="mb-3 px-1"
      >
        返回工作台
      </v-btn>

      <div class="mb-6">
        <div class="t-eyebrow mb-1">{{ OVERLINES[kind] }}</div>
        <div class="d-flex align-center flex-wrap ga-3">
          <h1 class="t-page-title" style="font-size: 27px">{{ TITLES[kind] }}</h1>
          <span v-if="projectName" class="t-meta">{{ projectName }}</span>
          <template v-if="kind === 'charter'">
            <v-spacer />
            <span v-if="saving" class="t-meta">保存中…</span>
            <span v-else-if="savedAt" class="d-inline-flex align-center ga-1 c-faint" style="font-size: 12px">
              <span class="status-dot status-dot--ok" />已保存
            </span>
            <span v-else-if="charterDirty" class="t-meta">未保存</span>
          </template>
        </div>
        <div v-if="kind === 'charter'" class="t-meta mt-1">改了就等于给芝士下指令</div>
      </div>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4">
        {{ error }}
      </v-alert>

      <template v-else>
        <!-- ===== 记忆: what 芝士 remembers, human-prunable ===== -->
        <template v-if="kind === 'memory'">
          <div v-if="memoryEntries.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
            <v-icon size="28" class="text-disabled mb-2">mdi-brain</v-icon>
            <div>暂无记忆</div>
            <div class="text-caption mt-1">对话里说「记住……」，或它自己判断重要时，会写进这里</div>
          </div>
          <v-card v-for="e in memoryEntries" :key="e.id" class="memory-card mb-2" variant="flat">
            <div class="d-flex align-start ga-3 pa-3">
              <v-icon size="16" class="c-muted mt-1">
                {{ e.scope === 'user' ? 'mdi-account-outline' : 'mdi-source-repository' }}
              </v-icon>
              <div class="flex-grow-1">
                <div class="memory-card__content">{{ e.content }}</div>
                <div class="t-meta c-muted mt-1">
                  {{ e.scope === 'user' ? '个人记忆' : '项目记忆' }} · {{ relTime(e.created_at) }}
                </div>
              </div>
              <v-btn
                icon="mdi-delete-outline"
                size="x-small"
                variant="text"
                class="c-muted memory-card__del"
                title="删除这条记忆"
                @click="removeMemory(e.id)"
              />
            </div>
          </v-card>
        </template>

        <!-- ===== 章程: project root doc, read/edit with the rich tiptap
             editor — the SAME editing experience as the workspace doc panel
             (drag handle, tables, task lists, code highlighting), persisted via
             the same getDoc/putDoc API. ===== -->
        <template v-else-if="kind === 'charter'">
          <v-card class="charter-card">
            <div class="charter-body">
              <DocEditor
                v-if="rootTopicId"
                :topic-id="rootTopicId"
                :editable="true"
                placeholder="芝士还没写章程——它会在你定下项目方向后维护这份文档。你也可以直接在这里写，内容会自动保存。"
                @saving="onCharterSaving"
                @saved="onCharterSaved"
                @dirty="onCharterDirty"
                @error="onCharterError"
              />
              <div v-else class="text-medium-emphasis text-body-2 py-2">这个项目还没有可编辑的章程文档</div>
            </div>
          </v-card>
        </template>

        <!-- ===== 决策记录 ===== -->
        <template v-else-if="kind === 'decisions'">
          <div v-if="decisions.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
            <div>暂无决策记录</div>
            <div class="text-caption mt-1">芝士在协作中定下关键决策时会记到这里</div>
          </div>
          <div v-else class="d-flex flex-column ga-3">
            <v-card v-for="d in decisions" :key="d.id" class="decision-card">
              <div class="decision-bar" />
              <div class="pa-4">
                <div class="d-flex align-center ga-2 mb-2">
                  <v-icon size="17" class="c-faint"> mdi-clipboard-text-clock-outline </v-icon>
                  <span class="t-meta">{{ fmtDate(d.created_at) }}</span>
                  <v-spacer />
                  <v-btn
                    v-if="d.topic_id"
                    :to="topicTo(d.topic_id)"
                    size="x-small"
                    variant="text"
                    class="c-muted"
                    append-icon="mdi-arrow-top-right"
                  >
                    来自话题
                  </v-btn>
                </div>
                <div class="md-content text-body-2" v-html="renderMarkdown(d.content)" />
              </div>
            </v-card>
          </div>
        </template>

        <!-- ===== 周报集 ===== -->
        <template v-else>
          <div v-if="weeklies.length === 0" class="text-medium-emphasis text-body-2 py-6 text-center">
            <div>暂无周报</div>
            <div class="text-caption mt-1">周报由芝士定期产出</div>
          </div>
          <div v-else class="d-flex flex-column ga-3">
            <router-link v-for="t in weeklies" :key="t.id" class="weekly-link" :to="topicTo(t.id)">
              <v-card class="weekly-card">
                <div class="pa-4 d-flex align-center ga-3">
                  <v-icon size="20" class="c-faint"> mdi-calendar-week-outline </v-icon>
                  <div class="flex-grow-1" style="min-width: 0">
                    <div class="t-body text-truncate" style="font-weight: 500; color: var(--ink)">
                      {{ t.title }}
                    </div>
                    <div class="t-meta">{{ fmtDate(t.created_at) }}</div>
                  </div>
                  <v-icon size="18" class="c-faint">mdi-chevron-right</v-icon>
                </div>
              </v-card>
            </router-link>
          </div>
        </template>
      </template>
    </v-container>
  </div>
</template>

<style scoped>
.docs-page {
  background: var(--canvas);
}

/* 章程 card: a clean document sheet. */
.charter-card {
  background: var(--surface);
}
.charter-body {
  padding: 28px 32px;
}

/* 决策记录 cards: quiet left rule (源自原始话题) — neutral, not amber. */
.decision-card {
  position: relative;
  overflow: hidden;
}
.decision-bar {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--line-2);
}

/* 周报集 link cards. */
.weekly-link {
  text-decoration: none;
  color: inherit;
}
.weekly-card {
  transition: background 0.12s ease;
}
.weekly-card:hover {
  background: var(--fill);
}

/* Rendered markdown (mirrors OverviewView's .md-content). */
.md-content {
  font-size: 0.95rem;
  line-height: 1.7;
}
.md-content :deep(p) {
  margin: 0 0 8px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3) {
  font-weight: 600;
  margin: 14px 0 6px;
}
.md-content :deep(h1) {
  font-size: 1.4em;
}
.md-content :deep(h2) {
  font-size: 1.2em;
}
.md-content :deep(h3) {
  font-size: 1.05em;
}
.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.md-content :deep(li) {
  margin: 2px 0;
}
.md-content :deep(li::marker) {
  color: var(--faint);
}
.md-content :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
}
.md-content :deep(a:hover) {
  text-decoration: underline;
}
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--fill);
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
}
.md-content :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
</style>

<style scoped>
.memory-card {
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}
.memory-card__content {
  font-size: 0.9rem;
  line-height: 1.55;
  white-space: pre-wrap;
}
.memory-card__del {
  opacity: 0;
  transition: opacity 0.12s;
}
.memory-card:hover .memory-card__del {
  opacity: 1;
}
</style>
