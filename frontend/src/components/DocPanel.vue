<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import type { Node as PMNode } from '@tiptap/pm/model'
import StarterKit from '@tiptap/starter-kit'
import { Markdown } from '@tiptap/markdown'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import CheeseAvatar from './CheeseAvatar.vue'
import {
  getDoc,
  getGitDiff,
  getGitLog,
  getProjectUsage,
  getTopicUsage,
  getTranscript,
  listFiles,
  putDoc,
  readFile,
} from '../api'
import type {
  Block,
  FileContent,
  GitCommit,
  Topic,
  UsageStats,
  WorkspaceFile,
} from '../types'

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }) as string)
}

// The living doc is the core interface (spec §2.2): an AI-maintained markdown
// document the user can also edit ("改文档即指令"). Stored as markdown, so the
// editor reads markdown in (contentType: 'markdown') and serializes markdown out
// (editor.getMarkdown()).
const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // Bumped by the parent on AI activity (turn-done / update_doc tool) so the
    // panel reloads the doc 芝士 just wrote. See WorkspaceView activityTick.
    activityTick: number
    // 施工现场: this topic's AI tool-action log, shown in the 现场 drawer.
    worklog?: string[]
  }>(),
  { worklog: () => [] },
)

// ---- 按需打开的工具 (spec §7.1): slide-out tool drawer ----
interface ToolDef {
  key: string
  label: string
  icon: string
}
const TOOLS: ToolDef[] = [
  { key: 'preview', label: '预览', icon: 'mdi-eye-outline' },
  { key: 'git', label: 'Git', icon: 'mdi-source-branch' },
  { key: 'site', label: '现场', icon: 'mdi-hammer-wrench' },
  { key: 'files', label: '文件', icon: 'mdi-folder-outline' },
  { key: 'resources', label: '资源', icon: 'mdi-link-variant' },
]
const openTool = ref<string | null>(null)
const drawerOpen = ref(false)
// 钉住: pinned tools dock beside the doc (doc shrinks) instead of floating as a
// temporary overlay. Persisted so it sticks across sessions.
const pinned = ref<boolean>(localStorage.getItem('cheesex.toolPinned') === '1')
watch(pinned, (v) => localStorage.setItem('cheesex.toolPinned', v ? '1' : '0'))
function togglePin() {
  pinned.value = !pinned.value
}
function closeTool() {
  drawerOpen.value = false
  openTool.value = null
}
const activeToolLabel = () =>
  TOOLS.find((t) => t.key === openTool.value)?.label ?? ''

const projectId = computed<string | null>(() => props.topic?.project_id ?? null)

// ---- Per-tool data (lazy-loaded when its drawer opens) ----
const toolLoading = ref(false)
const toolError = ref<string | null>(null)

// 现场: read-only transcript timeline.
const transcript = ref<Block[]>([])
// Git: commit log + working-tree diff.
const gitCommits = ref<GitCommit[]>([])
const gitDiff = ref<string>('')
// 文件: file list + the file the user opened.
const files = ref<WorkspaceFile[]>([])
const openFile = ref<FileContent | null>(null)
// 资源: usage for this topic vs the whole project.
const topicUsage = ref<UsageStats | null>(null)
const projectUsage = ref<UsageStats | null>(null)

// 预览: first *.html in the file list, rendered in an iframe.
const previewFile = ref<FileContent | null>(null)

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function fmtCost(n: number): string {
  return `$${n.toFixed(4)}`
}

async function loadTool(key: string) {
  const tid = props.topic?.id
  const pid = projectId.value
  if (!tid || !pid) return
  toolLoading.value = true
  toolError.value = null
  try {
    if (key === 'site') {
      transcript.value = (await getTranscript(tid)).data
    } else if (key === 'git') {
      // A fresh repo with no commits makes git log fail (422); tolerate it so
      // the diff still renders instead of the whole drawer showing an error.
      const [log, diff] = await Promise.all([
        getGitLog(pid).catch(() => ({ data: [] as GitCommit[], total: 0 })),
        getGitDiff(pid),
      ])
      // Guard against a topic switch mid-flight.
      if (props.topic?.id !== tid) return
      gitCommits.value = log.data
      gitDiff.value = diff.diff
    } else if (key === 'files') {
      files.value = (await listFiles(pid)).data
      openFile.value = null
    } else if (key === 'resources') {
      const [tu, pu] = await Promise.all([
        getTopicUsage(tid),
        getProjectUsage(pid),
      ])
      if (props.topic?.id !== tid) return
      topicUsage.value = tu
      projectUsage.value = pu
    } else if (key === 'preview') {
      const list = (await listFiles(pid)).data
      if (props.topic?.id !== tid) return
      files.value = list
      const html = list.find((f) => f.path.toLowerCase().endsWith('.html'))
      previewFile.value = html ? await readFile(pid, html.path) : null
    }
  } catch (e) {
    toolError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    if (props.topic?.id === tid) toolLoading.value = false
  }
}

async function selectFile(path: string) {
  const pid = projectId.value
  if (!pid) return
  toolError.value = null
  try {
    openFile.value = await readFile(pid, path)
  } catch (e) {
    toolError.value = e instanceof Error ? e.message : '读取文件失败'
  }
}

function toggleTool(key: string) {
  if (openTool.value === key && drawerOpen.value) {
    drawerOpen.value = false
    openTool.value = null
  } else {
    openTool.value = key
    drawerOpen.value = true
    loadTool(key)
  }
}

function authorLabel(b: Block): string {
  return b.author_type === 'ai' ? '芝士' : b.author
}

function fmtTime(iso: string): string {
  // Local HH:mm, not raw UTC slice.
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}

// 施工现场 tool-event lines: backend stores "verb\npreview"; legacy rows are
// "🔧 toolname". Split into the action verb and an optional argument preview.
const LEGACY_VERB: Record<string, string> = {
  create_subtopic: '拆出子话题',
  update_doc: '更新文档',
  remember: '记入记忆',
  notify: '发送通知',
  request_accept: '递出验收卡',
  return_conclusion: '回流结论',
  pin_milestone: '钉里程碑',
  write_file: '写文件',
  record_decision: '记录决策',
}
function eventVerb(content: string): string {
  const first = (content.split('\n')[0] || '').replace(/^🔧\s*/, '')
  return LEGACY_VERB[first] ?? first
}
function eventArg(content: string): string {
  const nl = content.indexOf('\n')
  return nl >= 0 ? content.slice(nl + 1).trim() : ''
}

const AUTHOR = 'user-1'
const PLACEHOLDER = '芝士会在这里维护文档，你也可以直接编辑'

const editable = ref(true)
const loading = ref(false)
const saving = ref(false)
const savedAt = ref<number | null>(null)
const errorMsg = ref<string | null>(null)

// Last markdown we know is persisted on the server. Used to (a) skip no-op
// saves and (b) detect whether an incoming reload actually changed the doc, so
// we don't clobber the user's in-progress local edits on every activity tick.
const lastSavedMarkdown = ref<string>('')
const dirty = ref(false)

const editor = useEditor({
  content: '',
  extensions: [StarterKit, Markdown],
  editable: editable.value,
  editorProps: {
    attributes: { class: 'doc-prose' },
  },
  onUpdate: () => {
    // User typing marks the doc dirty; saved indicator clears.
    if (loadingFromServer.value) return
    dirty.value = true
    savedAt.value = null
  },
})

// Guard: when we programmatically setContent from a server reload we don't want
// onUpdate to flag the doc as dirty.
const loadingFromServer = ref(false)

// ---- Block handles (Feishu docs): a real drag-handle (⠿ to reorder) + a ＋ to
// insert a block below. The DragHandle component tracks which block the cursor
// is over via onNodeChange; we keep that block's position to insert after it. ----
const hoverPos = ref<number | null>(null)
const hoverNodeSize = ref<number>(0)

function onDocNodeChange(data: { node: PMNode | null; pos: number }) {
  hoverPos.value = data.node ? data.pos : null
  hoverNodeSize.value = data.node?.nodeSize ?? 0
}

function addBlockBelow() {
  const ed = editor.value
  if (!ed || hoverPos.value == null) return
  const insertAt = hoverPos.value + hoverNodeSize.value
  ed.chain()
    .focus()
    .insertContentAt(insertAt, { type: 'paragraph' })
    .setTextSelection(insertAt + 1)
    .run()
}

function setEditorMarkdown(md: string) {
  const ed = editor.value
  if (!ed) return
  loadingFromServer.value = true
  ed.commands.setContent(md, { contentType: 'markdown' })
  loadingFromServer.value = false
}

async function loadDoc(topicId: string) {
  errorMsg.value = null
  loading.value = true
  try {
    const block = await getDoc(topicId)
    // Avoid races on fast topic switching.
    if (props.topic?.id !== topicId) return
    const md = block?.content ?? ''
    lastSavedMarkdown.value = md
    setEditorMarkdown(md)
    dirty.value = false
    savedAt.value = null
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载文档失败'
  } finally {
    if (props.topic?.id === topicId) loading.value = false
  }
}

// Reload triggered by AI activity. Don't clobber unsaved local edits: only pull
// the server version in if the user hasn't touched the doc since last save.
async function reloadFromActivity(topicId: string) {
  if (dirty.value) return
  try {
    const block = await getDoc(topicId)
    if (props.topic?.id !== topicId) return
    const md = block?.content ?? ''
    if (md !== lastSavedMarkdown.value) {
      lastSavedMarkdown.value = md
      setEditorMarkdown(md)
      savedAt.value = null
    }
  } catch {
    // Silent: activity-driven refresh is best-effort.
  }
}

async function save() {
  const ed = editor.value
  const topic = props.topic
  if (!ed || !topic) return
  const md = ed.getMarkdown()
  if (md === lastSavedMarkdown.value) {
    dirty.value = false
    return
  }
  saving.value = true
  errorMsg.value = null
  try {
    await putDoc(topic.id, md, AUTHOR)
    lastSavedMarkdown.value = md
    dirty.value = false
    savedAt.value = Date.now()
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

function onBlur() {
  if (dirty.value) save()
}

function toggleEditable() {
  editable.value = !editable.value
  editor.value?.setEditable(editable.value)
}

// Topic switch: full reload.
watch(
  () => props.topic?.id,
  (id) => {
    if (id) loadDoc(id)
    else {
      lastSavedMarkdown.value = ''
      dirty.value = false
      setEditorMarkdown('')
    }
    // Close the drawer on topic switch so it doesn't carry over.
    openTool.value = null
    drawerOpen.value = false
  },
  { immediate: true },
)

// AI activity: soft reload (respects unsaved edits).
watch(
  () => props.activityTick,
  () => {
    const id = props.topic?.id
    if (id) reloadFromActivity(id)
  },
)

onBeforeUnmount(() => {
  editor.value?.destroy()
})
</script>

<template>
  <div class="doc d-flex flex-column fill-height" style="position: relative">
    <div
      v-if="!topic"
      class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis"
    >
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>选择一个话题查看文档</div>
      </div>
    </div>

    <template v-else>
      <!-- Header -->
      <v-toolbar density="comfortable" flat color="surface" border="b">
        <v-toolbar-title class="t-title">
          <v-icon size="17" class="me-1 c-faint">mdi-file-document-outline</v-icon>
          文档
        </v-toolbar-title>
        <v-spacer />

        <span v-if="loading" class="t-meta me-2">加载中…</span>
        <span v-else-if="saving" class="t-meta me-2">保存中…</span>
        <span
          v-else-if="savedAt"
          class="d-inline-flex align-center ga-1 c-faint me-2"
          style="font-size: 12px"
        >
          <span class="status-dot status-dot--ok" />已保存
        </span>
        <span v-else-if="dirty" class="t-meta me-2">未保存</span>

        <v-btn size="small" variant="text" class="me-1 c-muted" @click="toggleEditable">
          {{ editable ? '只读' : '编辑' }}
        </v-btn>
        <v-btn
          v-if="editable"
          size="small"
          color="primary"
          variant="flat"
          class="me-2"
          :disabled="saving || !dirty"
          @click="save"
        >
          保存
        </v-btn>

        <v-divider vertical class="mx-1" />

        <!-- 右上角工具图标: 按需打开工具，从右侧滑出 (spec §7.1) -->
        <v-btn
          v-for="t in TOOLS"
          :key="t.key"
          :icon="t.icon"
          size="small"
          variant="text"
          :class="openTool === t.key && drawerOpen ? 'tool-btn--active' : 'c-muted'"
          :title="t.label"
          @click="toggleTool(t.key)"
        />
      </v-toolbar>

      <!-- Stage: the editor + (optionally) a docked tool panel beside it. -->
      <div class="doc-stage flex-grow-1">
      <!-- Editor surface — a Feishu Docs page: white, padded, centered column. -->
      <div
        class="doc-body overflow-y-auto"
        :class="{ readonly: !editable }"
        @focusout="onBlur"
      >
        <div class="doc-page">
          <!-- Large document title (Feishu Docs), = the topic title -->
          <h1 class="doc-page__title">{{ topic.title }}</h1>
          <div class="doc-editor-wrap">
            <EditorContent v-if="editor" :editor="editor" class="doc-editor" />
            <!-- Real block handles: ⠿ drags to reorder, ＋ inserts a block below.
                 Only in edit mode. -->
            <DragHandle
              v-if="editor && editable"
              :editor="editor"
              :on-node-change="onDocNodeChange"
              class="doc-handle"
            >
              <button
                type="button"
                class="doc-handle__btn doc-handle__add"
                title="在下方插入块"
                draggable="false"
                @dragstart.stop.prevent
                @click="addBlockBelow"
              >
                +
              </button>
              <span class="doc-handle__btn doc-handle__grip" title="拖动以排序">⠿</span>
            </DragHandle>
            <p v-if="editor && editor.isEmpty && !loading" class="placeholder">
              {{ PLACEHOLDER }}
            </p>
          </div>
        </div>
      </div>

      <!-- Tool panel (spec §7.1): floats over the doc for a quick peek, or docks
           beside it when 钉住 (pinned). The scrim closes a floating panel on an
           outside click; a pinned panel stays and the doc makes room for it. -->
      <div v-if="drawerOpen && !pinned" class="tool-scrim" @click="closeTool" />
      <transition name="tool-slide">
        <aside
          v-if="drawerOpen"
          class="tool-panel"
          :class="pinned ? 'tool-panel--pinned' : 'tool-panel--float'"
        >
          <div class="tool-panel__head">
            <span class="tool-panel__title">{{ activeToolLabel() }}</span>
            <v-spacer />
            <v-btn
              :icon="pinned ? 'mdi-pin' : 'mdi-pin-outline'"
              size="small"
              variant="text"
              :title="pinned ? '取消钉住' : '钉住（停靠在文档旁）'"
              :class="pinned ? 'tool-btn--active' : 'c-muted'"
              @click="togglePin"
            />
            <v-btn icon="mdi-close" variant="text" size="small" class="c-muted" @click="closeTool" />
          </div>
          <v-divider />

          <div class="tool-content">
          <div
            v-if="toolLoading"
            class="d-flex justify-center py-8"
          >
            <v-progress-circular indeterminate color="primary" size="28" />
          </div>
          <v-alert
            v-else-if="toolError"
            type="error"
            density="compact"
            class="ma-4"
          >
            {{ toolError }}
          </v-alert>

          <!-- 现场: read-only transcript timeline (芝士 messages + 🔧 events) -->
          <template v-else-if="openTool === 'site'">
            <div
              v-if="transcript.length === 0"
              class="text-center text-medium-emphasis py-6"
            >
              本话题暂无施工记录
            </div>
            <div v-else class="site-log pa-3">
              <template v-for="b in transcript" :key="b.id">
                <!-- Tool action — Claude Code style: ● verb + ⎿ arg preview -->
                <div v-if="b.kind === 'event'" class="site-act">
                  <span class="site-act__dot">●</span>
                  <div class="site-act__body">
                    <span class="site-act__verb">{{ eventVerb(b.content) }}</span>
                    <div v-if="eventArg(b.content)" class="site-act__arg">
                      ⎿ {{ eventArg(b.content) }}
                    </div>
                  </div>
                  <span class="site-act__time">{{ fmtTime(b.created_at) }}</span>
                </div>
                <!-- 芝士 speaks — shown as a person, with avatar (like the chat) -->
                <div v-else class="site-msg">
                  <CheeseAvatar :size="26" class="site-msg__av" />
                  <div class="site-msg__main">
                    <div class="site-msg__meta">
                      <span class="site-msg__name">{{ authorLabel(b) }}</span>
                      <span class="t-meta">{{ fmtTime(b.created_at) }}</span>
                    </div>
                    <div class="md-content text-body-2" v-html="renderMarkdown(b.content)" />
                  </div>
                </div>
              </template>
            </div>
          </template>

          <!-- Git: commit log + working-tree diff -->
          <template v-else-if="openTool === 'git'">
            <div class="pa-3">
              <div class="t-eyebrow mb-2">提交记录</div>
              <div
                v-if="gitCommits.length === 0"
                class="text-medium-emphasis text-body-2 mb-3"
              >
                暂无提交
              </div>
              <v-list v-else density="compact" class="py-0 mb-3">
                <v-list-item v-for="c in gitCommits" :key="c.hash" class="px-0">
                  <template #prepend>
                    <v-icon size="14" class="me-1 c-faint">mdi-source-commit</v-icon>
                  </template>
                  <v-list-item-title class="text-body-2">
                    {{ c.message }}
                  </v-list-item-title>
                  <v-list-item-subtitle class="text-caption">
                    {{ c.hash.slice(0, 7) }} · {{ c.author }}
                  </v-list-item-subtitle>
                </v-list-item>
              </v-list>

              <v-divider class="mb-3" />
              <div class="t-eyebrow mb-2">改动 diff</div>
              <pre v-if="gitDiff.trim()" class="code-pre">{{ gitDiff }}</pre>
              <div v-else class="text-medium-emphasis text-body-2">
                工作区干净，无未提交改动
              </div>
            </div>
          </template>

          <!-- 文件: file list → click to read a file -->
          <template v-else-if="openTool === 'files'">
            <div class="pa-3">
              <template v-if="openFile">
                <v-btn
                  size="x-small"
                  variant="text"
                  prepend-icon="mdi-arrow-left"
                  class="mb-2"
                  @click="openFile = null"
                >
                  返回文件列表
                </v-btn>
                <div class="text-body-2 font-weight-medium mb-2">
                  {{ openFile.path }}
                </div>
                <pre class="code-pre">{{ openFile.content }}</pre>
              </template>
              <template v-else>
                <div
                  v-if="files.length === 0"
                  class="text-center text-medium-emphasis py-6"
                >
                  暂无文件
                </div>
                <v-list v-else density="compact" class="py-0">
                  <v-list-item
                    v-for="f in files"
                    :key="f.path"
                    class="px-0"
                    @click="selectFile(f.path)"
                  >
                    <template #prepend>
                      <v-icon size="16" class="me-1">mdi-file-outline</v-icon>
                    </template>
                    <v-list-item-title class="text-body-2">
                      {{ f.path }}
                    </v-list-item-title>
                    <template #append>
                      <span class="text-caption text-medium-emphasis">
                        {{ fmtBytes(f.bytes) }}
                      </span>
                    </template>
                  </v-list-item>
                </v-list>
              </template>
            </div>
          </template>

          <!-- 资源: usage stat rows (本话题 vs 全项目) -->
          <template v-else-if="openTool === 'resources'">
            <div class="pa-3">
              <div
                v-for="row in [
                  { label: '本话题', u: topicUsage },
                  { label: '全项目', u: projectUsage },
                ]"
                :key="row.label"
                class="mb-4"
              >
                <div class="t-eyebrow mb-2">
                  {{ row.label }}
                </div>
                <div v-if="row.u" class="usage-grid">
                  <div class="usage-cell">
                    <div class="usage-num">{{ row.u.turns }}</div>
                    <div class="t-meta">轮次</div>
                  </div>
                  <div class="usage-cell">
                    <div class="usage-num">{{ row.u.total_tokens }}</div>
                    <div class="t-meta">总 token</div>
                  </div>
                  <div class="usage-cell">
                    <div class="usage-num">{{ row.u.input_tokens }}</div>
                    <div class="t-meta">输入</div>
                  </div>
                  <div class="usage-cell">
                    <div class="usage-num">{{ row.u.output_tokens }}</div>
                    <div class="t-meta">输出</div>
                  </div>
                  <div class="usage-cell">
                    <div class="usage-num">{{ fmtCost(row.u.cost_usd) }}</div>
                    <div class="t-meta">费用</div>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- 预览: render the first *.html product in an iframe -->
          <template v-else-if="openTool === 'preview'">
            <div v-if="previewFile" class="preview-wrap">
              <div class="text-caption text-medium-emphasis px-3 pt-2">
                {{ previewFile.path }}
              </div>
              <iframe
                class="preview-frame"
                :srcdoc="previewFile.content"
                sandbox="allow-same-origin"
              />
            </div>
            <div v-else class="text-center text-medium-emphasis py-8">
              <v-icon size="32" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
              <div>暂无可预览的产物</div>
            </div>
          </template>
          </div>
        </aside>
      </transition>
      </div><!-- /.doc-stage -->

      <v-alert
        v-if="errorMsg"
        type="error"
        density="compact"
        class="ma-3 mt-0"
        closable
        @click:close="errorMsg = null"
      >
        {{ errorMsg }}
      </v-alert>
    </template>
  </div>
</template>

<style scoped>
.doc {
  /* In the split workspace the doc is a full white surface that fills the pane —
     not a floating card on a gray canvas (which left gray gutters around it). */
  background: var(--surface);
}
.doc-body {
  padding: 8px 0 80px;
  position: relative;
  display: flex;
  justify-content: center;
}
/* The document column: white surface (inherits .doc), text capped for
   readability and centered. No card border/radius — it IS the surface. */
.doc-page {
  width: 100%;
  max-width: 820px;
  background: transparent;
  padding: 32px 48px 72px;
}
.doc-page__title {
  max-width: 720px;
  margin: 0 auto 0.4em;
  font-family: var(--font-display);
  font-size: 1.85rem;
  font-weight: 650;
  line-height: 1.3;
  letter-spacing: -0.02em;
  color: var(--ink);
}

/* Tool icon when its drawer is open — neutral ink, not amber. */
.tool-btn--active {
  color: var(--ink) !important;
  background: var(--fill);
}
.doc-editor-wrap {
  position: relative;
}
.placeholder {
  position: absolute;
  top: 0;
  left: 0;
  color: rgba(var(--v-theme-on-surface), 0.4);
  pointer-events: none;
  margin: 0;
}
/* Stage holds the editor and, when pinned, the docked tool panel beside it. */
.doc-stage {
  position: relative;
  display: flex;
  min-height: 0;
  overflow: hidden;
}
.doc-body {
  flex: 1 1 auto;
  min-width: 0;
}
/* Tool panel — one markup, positioned by mode. */
.tool-panel {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  min-height: 0;
}
/* Floating (quick peek): overlays the right of the doc with a soft shadow. */
.tool-panel--float {
  position: absolute;
  inset: 0 0 0 auto;
  width: 380px;
  max-width: 86%;
  border-left: 1px solid var(--line);
  box-shadow: -10px 0 28px rgba(16, 18, 22, 0.08);
  z-index: 6;
}
/* Pinned (钉住): in-flow column — the doc shrinks to make room. */
.tool-panel--pinned {
  position: relative;
  flex: 0 0 340px;
  border-left: 1px solid var(--line);
}
.tool-panel__head {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 48px;
  padding: 0 6px 0 14px;
  flex: 0 0 auto;
}
.tool-panel__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.tool-content {
  flex: 1 1 auto;
  overflow-y: auto;
}

/* 施工现场 timeline: 芝士 speaks (avatar + text), tools render as Claude-Code
   action lines (● verb, ⎿ argument preview). */
.site-log {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.site-msg {
  display: flex;
  gap: 8px;
}
.site-msg__av {
  flex: 0 0 auto;
  margin-top: 1px;
}
.site-msg__main {
  flex: 1 1 auto;
  min-width: 0;
}
.site-msg__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}
.site-msg__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.site-act {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.5;
}
.site-act__dot {
  color: var(--accent);
  flex: 0 0 auto;
}
.site-act__body {
  flex: 1 1 auto;
  min-width: 0;
}
.site-act__verb {
  color: var(--text);
}
.site-act__arg {
  color: var(--faint);
  white-space: pre-wrap;
  word-break: break-word;
  margin-top: 1px;
}
.site-act__time {
  flex: 0 0 auto;
  color: var(--faint);
  font-size: 11px;
  font-family: var(--font-mono);
}
/* 现场 is a transcript, not a doc — tame heading sizes inside 芝士 messages so
   they read like chat, not a document. */
.site-msg__main :deep(h1),
.site-msg__main :deep(h2),
.site-msg__main :deep(h3) {
  font-size: 1em;
  font-weight: 600;
  margin: 6px 0 2px;
  color: var(--ink);
}
/* Transparent scrim: an outside click dismisses the floating panel. */
.tool-scrim {
  position: absolute;
  inset: 0;
  z-index: 5;
}
.tool-slide-enter-active,
.tool-slide-leave-active {
  transition: transform 0.18s ease, opacity 0.18s ease;
}
.tool-slide-enter-from,
.tool-slide-leave-to {
  transform: translateX(12px);
  opacity: 0;
}
.code-pre {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  line-height: 1.5;
  background: var(--fill);
  color: var(--text);
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
  white-space: pre;
  margin: 0;
}
.usage-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.usage-cell {
  background: var(--fill);
  border-radius: 8px;
  padding: 8px 12px;
}
.usage-num {
  font-family: var(--font-mono);
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.preview-wrap {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.preview-frame {
  flex: 1 1 auto;
  width: 100%;
  border: none;
  background: #fff;
}
.md-content :deep(p) {
  margin: 0 0 6px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: 4px;
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--fill);
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
}

/* ProseMirror editable area — reads like a clean document column. */
.doc-editor :deep(.doc-prose) {
  outline: none;
  min-height: 240px;
  max-width: 720px;
  margin: 0 auto;
  line-height: 1.8;
  font-size: 16px;
  color: var(--text);
}
.doc-editor :deep(.doc-prose:focus) {
  outline: none;
}

/* Feishu-style left gutter block handles — REAL controls, not decoration.
   The DragHandle floats next to the hovered block (positioned by the extension).
   ＋ inserts a block below (click); ⠿ drags to reorder. */
.doc-handle {
  display: flex;
  align-items: center;
  gap: 1px;
}
.doc-handle__btn {
  width: 20px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  line-height: 1;
  color: var(--faint);
  background: transparent;
  border: none;
  border-radius: 5px;
  user-select: none;
  transition: background 0.12s ease, color 0.12s ease;
}
.doc-handle__add {
  cursor: pointer;
  font-size: 18px;
}
.doc-handle__grip {
  cursor: grab;
  letter-spacing: -2px;
}
.doc-handle__grip:active {
  cursor: grabbing;
}
.doc-handle__btn:hover {
  background: var(--fill);
  color: var(--muted);
}
.doc-editor :deep(h1) {
  font-size: 1.6em;
  font-weight: 600;
  letter-spacing: -0.015em;
  margin: 0.4em 0 0.45em;
}
.doc-editor :deep(h2) {
  font-size: 1.28em;
  font-weight: 600;
  margin: 1.1em 0 0.3em;
}
.doc-editor :deep(h3) {
  font-size: 1.1em;
  font-weight: 600;
  margin: 0.9em 0 0.3em;
}
.doc-editor :deep(p) {
  margin: 0 0 0.75em;
}
.doc-editor :deep(ul),
.doc-editor :deep(ol) {
  margin: 0.4em 0 0.75em;
  padding-left: 1.4em;
}
.doc-editor :deep(li) {
  margin: 0.25em 0;
}
.doc-editor :deep(li::marker) {
  color: var(--faint);
}
.doc-editor :deep(li p) {
  margin: 0;
}
.doc-editor :deep(strong) {
  font-weight: 600;
}
.doc-editor :deep(blockquote) {
  margin: 0.7em 0;
  padding: 0.1em 0 0.1em 16px;
  border-left: 2px solid rgba(var(--v-theme-on-surface), 0.2);
  color: rgba(var(--v-theme-on-surface), 0.7);
}
.doc-editor :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: 4px;
  font-size: 0.87em;
}
.doc-editor :deep(pre) {
  background: var(--fill);
  padding: 13px 15px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.7em 0;
}
.doc-editor :deep(pre) code {
  background: none;
  padding: 0;
}
.doc-editor :deep(hr) {
  border: none;
  border-top: 1px solid var(--line);
  margin: 1.2em 0;
}
.doc-editor :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
}
.doc-editor :deep(a:hover) {
  text-decoration: underline;
}
</style>
