<script setup lang="ts">
import { myHandle } from '../me'
import { summarizeActions } from '../lib/toolLabels'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import type { Editor as CoreEditor } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import StarterKit from '@tiptap/starter-kit'
import { Markdown } from '@tiptap/markdown'
import CheeseAvatar from './CheeseAvatar.vue'
import CodeEditor from './CodeEditor.vue'
import {
  addComment,
  getComments,
  getDoc,
  getDocNodes,
  getGitDiff,
  getGitLog,
  getPreview,
  getProjectUsage,
  getTopicUsage,
  getTranscript,
  listFiles,
  putDoc,
  readFile,
  writeFile,
  upgradeBlock,
} from '../api'
import type {
  Block,
  FileContent,
  GitCommit,
  Topic,
  UsageStats,
  WorkspaceFile,
} from '../types'

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
    worklog?: { label: string; text: string }[]
    // A turn is in flight — the 现场 live feed's newest line pulses.
    working?: boolean
    // Epoch ms when the current turn's first tool ran (drives the ⏱ elapsed).
    workingSince?: number | null
    // 专注模式 (spec §7.1): the doc spans the whole workspace (chat hidden).
    focus?: boolean
    // Project topics (A2): resolve a doc node's upgraded_to_topic_id to the
    // subtopic's title + live status for the in-place live-ref badge.
    topicList?: Topic[]
  }>(),
  {
    worklog: () => [],
    working: false,
    workingSince: null,
    focus: false,
    topicList: () => [],
  },
)

// 专注模式 toggle is owned by the parent (it hides the chat pane); we just ask.
// open-topic (A2): a doc live-ref chip was clicked — the parent navigates to the
// subtopic. topics-changed: a split created a subtopic — refresh the sidebar.
const emit = defineEmits<{
  (e: 'toggle-focus'): void
  (e: 'open-topic', topicId: string): void
  (e: 'topics-changed'): void
}>()

// B1 Phase 2 (cross-view link, panel-level): when a chat action that changed the
// doc is clicked, flash the document + scroll it into view — connecting the
// process (conversation) to the state (doc). Paragraph-level anchoring needs the
// node-rendered editor (follow-up).
const pulsing = ref(false)

// A top-level editor block that carries no server node: tiptap keeps a trailing
// empty paragraph after block content (e.g. a list or heading) so you can click
// below it to type. The server's node list has no such node, so we ignore it when
// aligning.
function isFillerBlock(el: HTMLElement): boolean {
  return el.tagName === 'P' && el.textContent?.trim() === ''
}

// The rendered top-level blocks that correspond to server nodes — the raw
// ProseMirror children minus any trailing filler paragraph(s) tiptap appends.
function contentBlocks(): HTMLElement[] {
  const els = Array.from(
    document.querySelectorAll('.doc-editor .ProseMirror > *'),
  ) as HTMLElement[]
  while (els.length && isFillerBlock(els[els.length - 1])) els.pop()
  return els
}

// Align the doc-node tree (GET /docs) with the rendered ProseMirror blocks. Both
// derive from the same doc in the same order, so a positional zip connects each
// node (id, turn_id) to its DOM element. We must NOT tag those elements:
// ProseMirror's contentDOM is editable and guarded by a MutationObserver that
// reverts any foreign attribute/class we add on the next microtask — so callers
// read positions from here and highlight via an overlay outside the editable
// region instead. Retries a few frames because tiptap commits its DOM
// asynchronously after setEditorMarkdown. Returns [] if the shapes never converge.
async function alignedDocBlocks(): Promise<{ node: Block; el: HTMLElement }[]> {
  const tid = props.topic?.id
  if (!tid) return []
  let nodes: Block[]
  try {
    nodes = (await getDocNodes(tid)).data
  } catch {
    return []
  }
  for (let attempt = 0; attempt < 20; attempt++) {
    await nextTick()
    const els = contentBlocks()
    if (els.length === nodes.length) {
      return nodes.map((node, i) => ({ node, el: els[i] }))
    }
    await new Promise((r) => window.setTimeout(r, 100))
  }
  return []
}

// Flash a set of editor blocks. We draw transient overlay rectangles positioned
// over the targets rather than styling the blocks — ProseMirror owns and defends
// its editable DOM, so any class we add there is reverted instantly. Overlays live
// in `.doc-editor-wrap` (position: relative) and never touch the editor.
async function flashBlocks(els: HTMLElement[]) {
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  if (els.length === 0 || !wrap) {
    pulse()
    return
  }
  els[0].scrollIntoView({ behavior: 'smooth', block: 'center' })
  // Read positions after the smooth-scroll settles enough to be visible;
  // getBoundingClientRect is read once, so the flash is anchored to where the
  // block is now (fine for a ~1.5s cue).
  await nextTick()
  const wrapRect = wrap.getBoundingClientRect()
  for (const el of els) {
    const r = el.getBoundingClientRect()
    const ov = document.createElement('div')
    ov.className = 'node-flash-overlay'
    ov.style.top = `${r.top - wrapRect.top}px`
    ov.style.left = `${r.left - wrapRect.left}px`
    ov.style.width = `${r.width}px`
    ov.style.height = `${r.height}px`
    wrap.appendChild(ov)
    window.setTimeout(() => ov.remove(), 1500)
  }
}

// B1 Phase 2: highlight the exact paragraphs a turn produced. Falls back to a
// whole-doc pulse (via flashBlocks) when the turn's blocks can't be located.
async function highlightTurn(turnId: string) {
  const aligned = await alignedDocBlocks()
  await flashBlocks(aligned.filter((a) => a.node.turn_id === turnId).map((a) => a.el))
}

// B4: highlight the single paragraph a comment is anchored to (by doc-node id).
async function highlightNode(nodeId: string) {
  const aligned = await alignedDocBlocks()
  await flashBlocks(aligned.filter((a) => a.node.id === nodeId).map((a) => a.el))
}

// --- A2 自上而下拆解: a doc paragraph becomes a nested subtopic, and stays in
// place as a live-ref that shows the subtopic's live status. Same positional
// overlay technique as the highlight — we anchor a badge beside the paragraph
// without mutating ProseMirror's DOM. ---
interface LiveRef {
  nodeId: string
  topicId: string
  top: number
  title: string
  status: string
}
const liveRefs = ref<LiveRef[]>([])

const STATUS_LABEL: Record<string, string> = {
  open: '进行中',
  in_progress: '进行中',
  active: '进行中',
  draft: '草稿',
  archived: '已完成',
  completed: '已完成',
}
function statusLabel(s: string): string {
  return STATUS_LABEL[s] ?? s
}

// Recompute the live-ref badge positions from the current doc render. Called after
// (re)loads and on AI activity, so a badge tracks its paragraph and its status
// stays fresh. Positions are relative to .doc-editor-wrap, which scrolls with the
// content — so no scroll listener is needed.
async function positionLiveRefs() {
  const aligned = await alignedDocBlocks()
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  if (!wrap) {
    liveRefs.value = []
    return
  }
  const wrapRect = wrap.getBoundingClientRect()
  liveRefs.value = aligned
    .filter((a) => a.node.upgraded_to_topic_id)
    .map((a) => {
      const sub = props.topicList.find(
        (t) => t.id === a.node.upgraded_to_topic_id,
      )
      return {
        nodeId: a.node.id,
        topicId: a.node.upgraded_to_topic_id as string,
        top: a.el.getBoundingClientRect().top - wrapRect.top,
        title: sub?.title ?? '子话题',
        status: sub?.status ?? '',
      }
    })
}

// "单独实现" on a doc block (A2): upgrade that paragraph's node into a nested
// subtopic. The node keeps its text and gains a live-ref link; we open the new
// subtopic and ask the parent to refresh the sidebar.
const splitBusy = ref(false)
async function splitNodeToSubtopic() {
  const ed = editor.value
  const tid = props.topic?.id
  if (!ed || !tid || hoverPos.value == null || splitBusy.value) return
  // The server node list must line up with what's on screen; unsaved edits would
  // desync the positional map, so require a clean doc first.
  if (dirty.value) {
    errorMsg.value = '请先保存文档，再拆解段落'
    return
  }
  // Map the hovered ProseMirror position → the top-level child index.
  let index = -1
  ed.state.doc.forEach((_node, offset, i) => {
    if (offset === hoverPos.value) index = i
  })
  if (index < 0 || index !== Math.round(index)) return
  splitBusy.value = true
  try {
    const nodes = (await getDocNodes(tid)).data
    // Compare against the content blocks (raw PM children minus tiptap's trailing
    // filler paragraph). hoverPos's child index counts from the top, so it lines
    // up with the server nodes for every real block; hovering the filler gives
    // index === nodes.length, which the `index >= nodes.length` check rejects.
    if (index >= nodes.length || nodes.length !== contentBlocks().length) {
      errorMsg.value = '文档结构已变化，请重试'
      return
    }
    const sub = await upgradeBlock(nodes[index].id, AUTHOR)
    emit('topics-changed')
    emit('open-topic', sub.id)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '拆解失败'
  } finally {
    splitBusy.value = false
  }
}

async function pulse() {
  document.querySelector('.doc-body')?.scrollTo({ top: 0, behavior: 'smooth' })
  pulsing.value = false
  await nextTick()
  pulsing.value = true
  window.setTimeout(() => {
    pulsing.value = false
  }, 1200)
}
defineExpose({ pulse, highlightTurn })

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
  { key: 'comments', label: '评论', icon: 'mdi-comment-outline' },
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

// One width for the tool drawer, shared by both float and pinned modes so
// toggling 钉住 never changes the drawer's width (it just docks in place).
// Persisted. Default 380 = the drawer's long-standing floating width.
const toolWidth = ref<number>(
  Number(localStorage.getItem('cheesex.toolWidth')) || 380,
)
watch(toolWidth, (w) => localStorage.setItem('cheesex.toolWidth', String(w)))
function startToolResize(e: MouseEvent) {
  e.preventDefault()
  const panel = (e.currentTarget as HTMLElement).parentElement
  const right = panel ? panel.getBoundingClientRect().right : window.innerWidth
  const move = (ev: MouseEvent) => {
    toolWidth.value = Math.min(760, Math.max(260, right - ev.clientX))
  }
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

// 评论 (B4): inline comments anchored to doc nodes. anchorNodes lists the doc's
// paragraphs so a comment can target one (reply_to = node id); the empty pick
// means a whole-doc comment.
const comments = ref<Block[]>([])
const anchorNodes = ref<Block[]>([])
const anchorId = ref<string | null>(null)
const newComment = ref('')
const commentBusy = ref(false)
// The text span the current draft is quoting (set from a doc selection, B4).
const pendingQuote = ref<string | null>(null)
const commentInputRef = ref<{ focus?: () => void } | null>(null)

// A short label for a doc node, used in the anchor picker and comment chips. The
// node's own content is either AI- or human-authored text; we only ever truncate
// it for display (never to derive semantics), which is allowed.
function nodeLabel(content: string): string {
  const t = content.replace(/^#+\s*/, '').trim()
  return t.length > 22 ? t.slice(0, 22) + '…' : t || '(空段落)'
}
const anchorOptions = computed(() => [
  { id: null as string | null, label: '整篇文档' },
  ...anchorNodes.value.map((n) => ({ id: n.id, label: nodeLabel(n.content) })),
])
// The paragraph a comment points at (or null for a whole-doc comment).
function commentAnchor(c: Block): Block | null {
  return c.reply_to ? anchorNodes.value.find((n) => n.id === c.reply_to) ?? null : null
}

async function loadComments(tid: string) {
  const [cs, ns] = await Promise.all([getComments(tid), getDocNodes(tid)])
  comments.value = cs.data
  anchorNodes.value = ns.data
}

async function submitComment() {
  const tid = props.topic?.id
  const text = newComment.value.trim()
  if (!tid || !text) return
  commentBusy.value = true
  try {
    await addComment(
      tid,
      text,
      AUTHOR,
      anchorId.value ?? undefined,
      pendingQuote.value ?? undefined,
    )
    await loadComments(tid)
    newComment.value = ''
    pendingQuote.value = null
  } catch (e) {
    toolError.value = e instanceof Error ? e.message : '评论失败'
  } finally {
    commentBusy.value = false
  }
}

// Open the 评论 drawer (used by the selection CTA). Loads comments the same way
// toggling the tool does.
async function openCommentTool() {
  openTool.value = 'comments'
  drawerOpen.value = true
  const tid = props.topic?.id
  if (tid) await loadComments(tid)
}

// 现场: read-only transcript timeline.
const transcript = ref<Block[]>([])
// Live-turn elapsed seconds (ticks while `working`).
const nowTick = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null
watch(
  () => props.working,
  (w) => {
    if (tickTimer) clearInterval(tickTimer)
    tickTimer = w ? setInterval(() => (nowTick.value = Date.now()), 1000) : null
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (tickTimer) clearInterval(tickTimer)
})
const liveElapsed = computed(() => {
  if (!props.working || !props.workingSince) return null
  return Math.max(0, Math.round((nowTick.value - props.workingSince) / 1000))
})
const liveSummary = computed(() =>
  summarizeActions(props.worklog.map((w) => w.label)),
)
// Git: commit log + working-tree diff.
const gitCommits = ref<GitCommit[]>([])
const gitDiff = ref<string>('')
// 文件: a two-pane browser — the file list stays visible on the left, the opened
// file loads into a code editor on the right (editable; save = 人改文件即指令).
const files = ref<WorkspaceFile[]>([])
const openPath = ref<string | null>(null)
const fileDraft = ref<string>('')
const fileSaved = ref<string>('') // last loaded/saved content, for the dirty flag
const fileSaving = ref(false)
const fileListOpen = ref(true) // the ☰ toggle hides the list for a wider editor
const fileDirty = computed(() => fileDraft.value !== fileSaved.value)

// 文件树: the backend returns a flat list of full relative paths; build a
// nested tree out of it (folders first, each level sorted by name), then
// flatten into render rows — only expanded folders contribute their subtrees.
// Default is fully COLLAPSED: open exactly what you need.
interface FileRow {
  type: 'dir' | 'file'
  path: string // full relative path (dir or file)
  name: string // last segment, what we display
  depth: number
  bytes: number
}
const expandedDirs = ref(new Set<string>())
function toggleDir(path: string) {
  const next = new Set(expandedDirs.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expandedDirs.value = next
}
const fileRows = computed<FileRow[]>(() => {
  interface DirNode {
    dirs: Map<string, DirNode>
    files: WorkspaceFile[]
  }
  const root: DirNode = { dirs: new Map(), files: [] }
  for (const f of files.value) {
    const parts = f.path.split('/')
    let node = root
    for (const part of parts.slice(0, -1)) {
      let child = node.dirs.get(part)
      if (!child) {
        child = { dirs: new Map(), files: [] }
        node.dirs.set(part, child)
      }
      node = child
    }
    node.files.push(f)
  }
  const rows: FileRow[] = []
  const walk = (node: DirNode, prefix: string, depth: number) => {
    for (const name of [...node.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
      const path = prefix ? `${prefix}/${name}` : name
      rows.push({ type: 'dir', path, name, depth, bytes: 0 })
      if (expandedDirs.value.has(path)) {
        walk(node.dirs.get(name)!, path, depth + 1)
      }
    }
    const sorted = [...node.files].sort((a, b) => a.path.localeCompare(b.path))
    for (const f of sorted) {
      rows.push({
        type: 'file',
        path: f.path,
        name: f.path.split('/').pop() ?? f.path,
        depth,
        bytes: f.bytes,
      })
    }
  }
  walk(root, '', 0)
  return rows
})
// 资源: usage for this topic vs the whole project.
const topicUsage = ref<UsageStats | null>(null)
const projectUsage = ref<UsageStats | null>(null)

// 预览 (spec §9.1): the artifact 芝士 pointed at (cheese artifact) — its file,
// mimeType, and whether it was AI-designated (vs. the first-*.html fallback).
const previewFile = ref<FileContent | null>(null)
const previewMime = ref<string>('text/html')
const previewNamed = ref(false)
// 运行环境预览: the agent declared a RUNNING app (cheese serve) — iframe its
// live-resolved localhost URL instead of rendering file content.
const previewAppUrl = ref<string | null>(null)
const previewAppNote = ref<string>('')

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
      files.value = (await listFiles(pid, tid)).data
      // Keep the open file if it still exists; otherwise open the first file.
      if (!openPath.value || !files.value.some((f) => f.path === openPath.value)) {
        openPath.value = null
        if (files.value.length) await selectFile(files.value[0].path)
      }
    } else if (key === 'resources') {
      const [tu, pu] = await Promise.all([
        getTopicUsage(tid),
        getProjectUsage(pid),
      ])
      if (props.topic?.id !== tid) return
      topicUsage.value = tu
      projectUsage.value = pu
    } else if (key === 'preview') {
      // 芝士 points at the current preview via `cheese artifact` (render-by-type,
      // spec §7.1/§9.1). NO guessing when it hasn't named one: the old
      // first-*.html fallback proudly served frontend/index.html — an SPA
      // shell that renders blank — which is exactly why the spec says the
      // platform never picks the preview itself.
      const art = await getPreview(tid).catch(() => null)
      if (props.topic?.id !== tid) return
      previewAppUrl.value = null
      previewAppNote.value = ''
      if (art && art.kind === 'app') {
        previewNamed.value = true
        previewAppNote.value = art.path
        previewAppUrl.value = art.url ?? null
        previewFile.value = null
      } else if (art) {
        previewNamed.value = true
        previewMime.value = art.mime || 'text/html'
        previewFile.value = await readFile(pid, art.path, tid).catch(() => null)
      } else {
        previewNamed.value = false
        previewFile.value = null
      }
    } else if (key === 'comments') {
      await loadComments(tid)
      if (props.topic?.id !== tid) return
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
    const f = await readFile(pid, path, props.topic?.id)
    openPath.value = path
    fileDraft.value = f.content
    fileSaved.value = f.content
  } catch (e) {
    toolError.value = e instanceof Error ? e.message : '读取文件失败'
  }
}

async function saveFile() {
  const pid = projectId.value
  if (!pid || !openPath.value || !fileDirty.value || fileSaving.value) return
  fileSaving.value = true
  toolError.value = null
  try {
    await writeFile(pid, openPath.value, fileDraft.value, props.topic?.id)
    fileSaved.value = fileDraft.value
  } catch (e) {
    toolError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    fileSaving.value = false
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

const AUTHOR = myHandle()
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
    commentCta.value = null // the doc changed under the selection; drop the CTA
  },
  onSelectionUpdate: ({ editor: ed }) => updateCommentCta(ed),
})

// B4 Feishu-style: a floating "评论" button that appears over a text selection in
// the doc. Clicking it opens the comment composer anchored to the selected
// paragraph, with the selected span quoted. Positioned inside .doc-editor-wrap
// (like the other overlays) so it scrolls with the content.
interface CommentCta {
  top: number
  left: number
  quote: string
  nodeIndex: number
}
const commentCta = ref<CommentCta | null>(null)

function updateCommentCta(ed: CoreEditor) {
  const { from, to, empty } = ed.state.selection
  if (empty || !editable.value) {
    commentCta.value = null
    return
  }
  const quote = ed.state.doc.textBetween(from, to, ' ').trim()
  if (!quote) {
    commentCta.value = null
    return
  }
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  if (!wrap) return
  const wrapRect = wrap.getBoundingClientRect()
  const start = ed.view.coordsAtPos(from)
  const end = ed.view.coordsAtPos(to)
  commentCta.value = {
    top: start.top - wrapRect.top - 38,
    left: Math.min(end.right, start.left + 240) - wrapRect.left,
    quote,
    // depth-0 index = the top-level block the selection starts in.
    nodeIndex: ed.state.selection.$from.index(0),
  }
}

async function commentOnSelection() {
  const ed = editor.value
  const tid = props.topic?.id
  const cta = commentCta.value
  if (!ed || !tid || !cta) return
  const nodes = (await getDocNodes(tid)).data
  // Same filler-tolerant alignment as split/highlight.
  if (cta.nodeIndex >= nodes.length || nodes.length !== contentBlocks().length) {
    // Fall back to a whole-doc comment if the structure can't be mapped.
    anchorId.value = null
  } else {
    anchorId.value = nodes[cta.nodeIndex].id
  }
  pendingQuote.value = cta.quote
  commentCta.value = null
  // Open the comments drawer and focus the composer.
  await openCommentTool()
  await nextTick()
  commentInputRef.value?.focus?.()
}

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
    positionLiveRefs() // A2: place in-place subtopic badges for the new doc
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
    positionLiveRefs() // A2: refresh subtopic badges + their live status
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

// A2: when the sidebar's topics change (a subtopic's status moved, or a new one
// was spawned), refresh the badges' titles/status without a full doc reload.
watch(
  () => props.topicList,
  () => {
    if (props.topic?.id) positionLiveRefs()
  },
  { deep: true },
)

// Keep badge positions correct when the panel is resized.
function onResize() {
  if (props.topic?.id) positionLiveRefs()
}
if (typeof window !== 'undefined') window.addEventListener('resize', onResize)

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') window.removeEventListener('resize', onResize)
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

        <!-- 专注模式: 文档占满工作区，隐藏对话栏 (spec §7.1) -->
        <v-btn
          :icon="props.focus ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
          size="small"
          variant="text"
          :class="props.focus ? 'tool-btn--active' : 'c-muted'"
          :title="props.focus ? '退出专注' : '专注模式（文档全幅）'"
          @click="emit('toggle-focus')"
        />

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
        <div class="doc-page" :class="{ 'doc-pulse': pulsing }">
          <!-- Large document title (Feishu Docs), = the topic title -->
          <h1 class="doc-page__title">{{ topic.title }}</h1>
          <div class="doc-editor-wrap">
            <EditorContent v-if="editor" :editor="editor" class="doc-editor" />
            <!-- B4 Feishu-style: select text in the doc → a floating 评论 button
                 appears over the selection. Click to comment on that span. -->
            <button
              v-if="commentCta"
              type="button"
              class="doc-comment-cta"
              :style="{ top: `${commentCta.top}px`, left: `${commentCta.left}px` }"
              title="评论选中内容"
              @mousedown.prevent
              @click="commentOnSelection"
            >
              <v-icon size="14">mdi-comment-plus-outline</v-icon>
              评论
            </button>
            <!-- A2 in-place live-refs: a badge beside any paragraph that was
                 upgraded into a subtopic, showing its live status. Click to open
                 the subtopic. Positioned over the doc without touching the editor. -->
            <button
              v-for="lr in liveRefs"
              :key="lr.nodeId"
              type="button"
              class="doc-liveref"
              :style="{ top: `${lr.top}px` }"
              :title="`子话题「${lr.title}」· ${statusLabel(lr.status)} — 点击打开`"
              @click="emit('open-topic', lr.topicId)"
            >
              <span class="doc-liveref__dot" :class="`is-${lr.status}`" />
              🧩 {{ lr.title }}
              <span class="doc-liveref__status">{{ statusLabel(lr.status) }}</span>
            </button>
            <!-- Real block handles: 🧩 splits the block into a subtopic, ⠿ drags to
                 reorder, ＋ inserts a block below. Only in edit mode. -->
            <DragHandle
              v-if="editor && editable"
              :editor="editor"
              :on-node-change="onDocNodeChange"
              class="doc-handle"
            >
              <button
                type="button"
                class="doc-handle__btn doc-handle__split"
                title="单独实现（拆成子话题）"
                draggable="false"
                :disabled="splitBusy"
                @dragstart.stop.prevent
                @click="splitNodeToSubtopic"
              >
                🧩
              </button>
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
          :style="pinned ? { flex: `0 0 ${toolWidth}px` } : { width: `${toolWidth}px` }"
        >
          <!-- Drag the left edge to resize the pinned drawer (width persisted). -->
          <div
            v-if="pinned"
            class="tool-resizer"
            title="拖动调整宽度"
            @mousedown="startToolResize"
          />
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
              v-if="transcript.length === 0 && worklog.length === 0"
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
                    <!-- Raw transcript text on purpose (决定: 现场内容改为raw):
                         现场 shows what 芝士 actually emitted — markdown syntax,
                         <@handle> tokens and all — like a Claude Code session,
                         NOT the rendered chat version. -->
                    <div class="site-msg__raw">{{ b.content }}</div>
                  </div>
                </div>
              </template>

              <!-- 本轮实时动作 (live feed): what 芝士 is doing RIGHT NOW —
                   newest line pulses; the list clears when the turn ends and
                   the persisted transcript above becomes the record. -->
              <template v-for="(act, i) in worklog" :key="'live-' + i">
                <div class="site-act">
                  <span
                    class="site-act__dot"
                    :class="{
                      'site-act__dot--live': working && i === worklog.length - 1,
                    }"
                  >●</span>
                  <div class="site-act__body">
                    <span class="site-act__verb">{{ act.text }}</span>
                  </div>
                </div>
              </template>
              <!-- 本轮聚合摘要 (Claude Code 风): deterministic counts + ⏱ -->
              <div v-if="working && worklog.length" class="site-summary">
                <span class="site-act__dot site-act__dot--live">●</span>
                <span>
                  {{ liveSummary }}
                  <template v-if="liveElapsed !== null">
                    （{{ liveElapsed }}s）
                  </template>
                </span>
              </div>
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

          <!-- 文件: two-pane — the list stays on the left, the opened file loads
               into a code editor on the right (editable; 保存 = 人改文件即指令). -->
          <template v-else-if="openTool === 'files'">
            <div class="file-tool">
              <div class="file-bar">
                <v-btn
                  icon
                  size="x-small"
                  variant="text"
                  class="file-icon-btn"
                  :class="{ 'file-icon-btn--on': fileListOpen }"
                  title="文件列表"
                  @click="fileListOpen = !fileListOpen"
                >
                  <v-icon size="18">mdi-format-list-bulleted</v-icon>
                </v-btn>
                <span class="file-bar__path" :title="openPath || ''">
                  {{ openPath || '未打开文件' }}
                </span>
                <span v-if="fileDirty" class="file-bar__dot" title="未保存" />
                <v-spacer />
                <v-btn
                  size="x-small"
                  variant="flat"
                  color="primary"
                  :loading="fileSaving"
                  :disabled="!fileDirty"
                  @click="saveFile"
                >
                  保存
                </v-btn>
              </div>
              <div class="file-body">
                <div v-if="fileListOpen" class="file-list">
                  <div
                    v-if="files.length === 0"
                    class="text-center c-faint py-6"
                    style="font-size: 0.8rem"
                  >
                    暂无文件
                  </div>
                  <template v-for="row in fileRows" :key="`${row.type}:${row.path}`">
                    <!-- folder row: click toggles expand/collapse -->
                    <button
                      v-if="row.type === 'dir'"
                      type="button"
                      class="file-item file-item--dir"
                      :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
                      :title="row.path"
                      @click="toggleDir(row.path)"
                    >
                      <v-icon size="13" class="c-muted">
                        {{ expandedDirs.has(row.path) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
                      </v-icon>
                      <v-icon size="13" class="me-1 c-muted">
                        {{ expandedDirs.has(row.path) ? 'mdi-folder-open-outline' : 'mdi-folder-outline' }}
                      </v-icon>
                      <span class="file-item__name">{{ row.name }}</span>
                    </button>
                    <!-- file row: shows only the file name, indented under its folder -->
                    <button
                      v-else
                      type="button"
                      class="file-item"
                      :class="{ 'file-item--active': openPath === row.path }"
                      :style="{ paddingLeft: `${8 + row.depth * 14 + 13}px` }"
                      :title="`${row.path} · ${fmtBytes(row.bytes)}`"
                      @click="selectFile(row.path)"
                    >
                      <v-icon size="13" class="me-1 c-muted">mdi-file-outline</v-icon>
                      <span class="file-item__name">{{ row.name }}</span>
                    </button>
                  </template>
                </div>
                <div class="file-editor">
                  <CodeEditor
                    v-if="openPath"
                    v-model="fileDraft"
                    :filename="openPath"
                    @save="saveFile"
                  />
                  <div
                    v-else
                    class="d-flex align-center justify-center fill-height c-faint"
                    style="font-size: 0.85rem"
                  >
                    选择左侧文件查看 / 编辑
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- 评论 (B4): inline comments on the living doc -->
          <template v-else-if="openTool === 'comments'">
            <div class="pa-3">
              <div
                v-if="comments.length === 0"
                class="text-center text-medium-emphasis py-6"
              >
                还没有评论
              </div>
              <div v-for="c in comments" :key="c.id" class="comment-item mb-3">
                <div class="text-caption c-muted mb-1">{{ c.author }}</div>
                <!-- Feishu-style quote: the exact span the comment was made on.
                     Click to scroll + flash the paragraph it lives in (B4). -->
                <button
                  v-if="c.anchor_quote"
                  type="button"
                  class="comment-quote mb-1"
                  :title="commentAnchor(c) ? '定位到该段' : ''"
                  @click="c.reply_to && highlightNode(c.reply_to)"
                >
                  {{ c.anchor_quote }}
                </button>
                <!-- otherwise, a plain paragraph-anchor chip (dropdown-picked). -->
                <button
                  v-else-if="commentAnchor(c)"
                  type="button"
                  class="comment-anchor mb-1"
                  @click="highlightNode(c.reply_to!)"
                >
                  <span class="mdi mdi-link-variant" />
                  {{ nodeLabel(commentAnchor(c)!.content) }}
                </button>
                <div class="text-body-2">{{ c.content }}</div>
              </div>
              <!-- draft quote: the selected span this comment will attach to -->
              <div v-if="pendingQuote" class="comment-quote-draft mt-2">
                <span class="comment-quote-draft__text">{{ pendingQuote }}</span>
                <v-icon
                  size="14"
                  class="comment-quote-draft__x"
                  title="取消引用"
                  @click="pendingQuote = null"
                >mdi-close</v-icon>
              </div>
              <!-- anchor picker: only for whole-doc / paragraph comments made
                   without a text selection (a selection sets the quote instead). -->
              <v-select
                v-if="!pendingQuote"
                v-model="anchorId"
                :items="anchorOptions"
                item-title="label"
                item-value="id"
                label="评论对象"
                variant="outlined"
                density="compact"
                hide-details
                class="mt-2"
              />
              <v-textarea
                ref="commentInputRef"
                v-model="newComment"
                :placeholder="pendingQuote ? '对选中内容评论…' : '写条评论…'"
                rows="2"
                auto-grow
                variant="outlined"
                density="compact"
                hide-details
                class="mt-2"
              />
              <v-btn
                size="small"
                color="primary"
                variant="flat"
                class="mt-2"
                :loading="commentBusy"
                :disabled="!newComment.trim()"
                @click="submitComment"
              >
                发表评论
              </v-btn>
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

          <!-- 预览 (spec §9.1): the artifact 芝士 pointed at (cheese artifact),
               rendered by its mimeType. Never guessed by the platform. -->
          <template v-else-if="openTool === 'preview'">
            <!-- 运行环境预览: live app in the topic's container -->
            <div v-if="previewAppUrl" class="preview-wrap">
              <div class="preview-bar text-caption px-3 pt-2">
                <span class="text-medium-emphasis">{{ previewAppNote }}</span>
                <v-chip size="x-small" variant="tonal" class="ms-2">运行中的应用</v-chip>
                <v-chip size="x-small" variant="outlined" class="ms-1">
                  {{ previewAppUrl }}
                </v-chip>
              </div>
              <!-- The app is on 127.0.0.1:<port> — already a DIFFERENT origin
                   from the platform, so allow-same-origin only lets the app be
                   itself (cookies/storage on its own origin), never us. -->
              <iframe
                class="preview-frame"
                :src="previewAppUrl"
                sandbox="allow-same-origin allow-scripts allow-forms"
              />
            </div>
            <div
              v-else-if="previewNamed && previewAppNote"
              class="text-center text-medium-emphasis py-8"
            >
              <v-icon size="32" class="text-disabled mb-2">mdi-lan-disconnect</v-icon>
              <div>应用暂时不在线</div>
              <div class="text-caption mt-1">
                芝士声明过一个运行中的应用，但它的容器当前没在跑——再 @ 它一次即可拉起。
              </div>
            </div>
            <div v-else-if="previewFile" class="preview-wrap">
              <div class="preview-bar text-caption px-3 pt-2">
                <span class="text-medium-emphasis">{{ previewFile.path }}</span>
                <v-chip
                  v-if="previewNamed"
                  size="x-small"
                  color="primary"
                  variant="tonal"
                  class="ms-2"
                >芝士指定</v-chip>
                <v-chip size="x-small" variant="outlined" class="ms-1">
                  {{ previewMime }}
                </v-chip>
              </div>
              <!-- allow-scripts WITHOUT allow-same-origin (Claude Artifacts
                   posture): interactive artifacts run their JS, but in an
                   opaque origin that cannot touch the platform page. -->
              <iframe
                class="preview-frame"
                :srcdoc="previewFile.content"
                sandbox="allow-scripts"
              />
            </div>
            <div v-else class="text-center text-medium-emphasis py-8">
              <v-icon size="32" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
              <div>芝士还没有指定预览</div>
              <div class="text-caption mt-1">
                它做出网页 / 图表等可看的产物时，会把成果放到这里。
                预览渲染的是自足的单文件产物；要跑整个应用（如 Vue 工程）
                属于"运行环境预览"，还没做。
              </div>
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
/* B1 Phase 2: flash the exact paragraph(s) a turn produced. Rendered as an
   overlay (not a class on the paragraph) because ProseMirror reverts foreign
   mutations to its editable DOM. `:deep` because these divs are created
   imperatively inside the (scoped) .doc-editor-wrap. */
.doc-editor-wrap :deep(.node-flash-overlay) {
  position: absolute;
  z-index: 3;
  pointer-events: none;
  border-radius: 5px;
  margin: -3px -8px;
  padding: 3px 8px;
  box-sizing: content-box;
  animation: nodeFlash 1.5s ease-out forwards;
}
@keyframes nodeFlash {
  0% {
    background: color-mix(in srgb, var(--accent) 24%, transparent);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
  }
  100% {
    background: transparent;
    box-shadow: 0 0 0 1px transparent;
  }
}
/* B4: a comment's anchor chip — click to flash the paragraph it targets. */
.comment-anchor {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 100%;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 0.72rem;
  line-height: 1.5;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.09);
  cursor: pointer;
  border: none;
  transition: background 0.15s;
}
.comment-anchor:hover {
  background: rgba(var(--v-theme-primary), 0.18);
}
.comment-item {
  border-left: 2px solid rgba(var(--v-border-color), 0.4);
  padding-left: 10px;
}
/* B4 Feishu-style: floating "评论" CTA over a text selection. */
.doc-comment-cta {
  position: absolute;
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 10px;
  border-radius: 8px;
  font-size: 0.74rem;
  color: #fff;
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.22);
  cursor: pointer;
  border: none;
  white-space: nowrap;
}
.doc-comment-cta:hover {
  filter: brightness(1.08);
}
/* B4: the quoted span shown on a saved comment (click → flash its paragraph). */
.comment-quote {
  display: block;
  text-align: left;
  max-width: 100%;
  padding: 3px 8px;
  border-left: 2px solid rgb(var(--v-theme-primary));
  border-radius: 0 4px 4px 0;
  background: rgba(var(--v-theme-primary), 0.07);
  color: var(--muted);
  font-size: 0.76rem;
  line-height: 1.5;
  cursor: pointer;
  border-top: none;
  border-right: none;
  border-bottom: none;
}
.comment-quote:hover {
  background: rgba(var(--v-theme-primary), 0.14);
}
/* B4: the draft quote in the composer, with a clear button. */
.comment-quote-draft {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 5px 8px;
  border-left: 2px solid rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.07);
  border-radius: 0 4px 4px 0;
}
.comment-quote-draft__text {
  flex: 1;
  font-size: 0.76rem;
  color: var(--muted);
  line-height: 1.5;
  max-height: 3em;
  overflow: hidden;
}
.comment-quote-draft__x {
  cursor: pointer;
  color: var(--faint);
}
.comment-quote-draft__x:hover {
  color: var(--muted);
}

/* B1 Phase 2: a brief highlight when a chat action points at the doc. */
.doc-pulse {
  animation: docPulse 1.2s ease-out;
}
@keyframes docPulse {
  0% {
    box-shadow: 0 0 0 3px var(--accent);
    background: color-mix(in srgb, var(--accent) 8%, transparent);
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
    background: transparent;
  }
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
  /* Hold the panel at its set width: without this a flex item defaults to
     min-width:auto and wide, non-wrapping file content (white-space:pre) would
     stretch the drawer past toolWidth. min-width:0 lets inner overflow-x scroll. */
  min-width: 0;
}
/* Floating (quick peek): overlays the right of the doc with a soft shadow. */
.tool-panel--float {
  position: absolute;
  inset: 0 0 0 auto;
  /* width set inline (= toolWidth, shared with pinned) so pinning is seamless */
  max-width: 86%;
  border-left: 1px solid var(--line);
  box-shadow: -10px 0 28px rgba(16, 18, 22, 0.08);
  z-index: 6;
}
/* Pinned (钉住): in-flow column at the same width — the doc shrinks to make room. */
.tool-panel--pinned {
  position: relative;
  border-left: 1px solid var(--line);
}
/* Left-edge drag handle for the pinned tool drawer. */
.tool-resizer {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -3px;
  width: 11px;
  cursor: col-resize;
  z-index: 7;
}
.tool-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 3px;
  width: 2px;
  background: transparent;
  transition: background 0.12s ease;
}
.tool-resizer:hover::after {
  background: var(--accent);
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
  /* Constrain width so a wide code file scrolls inside .code-pre instead of
     widening the whole drawer (pairs with .tool-panel min-width:0). */
  min-width: 0;
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
.site-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--line-2, #e3e3e3);
  font-size: 12px;
  color: var(--muted, #777);
}
.site-act__dot--live {
  color: rgb(var(--v-theme-primary));
  animation: site-pulse 1.2s ease-in-out infinite;
}
@keyframes site-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
@media (prefers-reduced-motion: reduce) {
  .site-act__dot--live { animation: none; }
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
/* 现场 is a transcript, not a doc — 芝士's messages are shown RAW (markdown
   source, <@handle> tokens intact), Claude Code style: mono + pre-wrap. */
.site-msg__raw {
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
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

/* 文件: a two-pane browser — list + Monaco editor. Light, to match the app.
   Fills the drawer height so the editor scrolls internally. */
.file-tool {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.file-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-bottom: 1px solid rgba(var(--v-border-color), 0.5);
  flex: 0 0 auto;
}
.file-bar__path {
  font-family: var(--font-mono);
  font-size: 0.76rem;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 52%;
}
.file-bar__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  flex: 0 0 auto;
}
.file-body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.file-list {
  flex: 0 0 150px;
  overflow-y: auto;
  background: var(--fill);
  border-right: 1px solid rgba(var(--v-border-color), 0.5);
  padding: 4px 0;
}
.file-item {
  display: flex;
  align-items: center;
  width: 100%;
  text-align: left;
  padding: 3px 8px 3px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--text);
}
.file-item--dir .file-item__name {
  font-weight: 500;
}
.file-item:hover {
  background: rgba(var(--v-border-color), 0.18);
}
.file-item--active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.file-item__name {
  font-family: var(--font-mono);
  font-size: 0.74rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-editor {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
}
.file-icon-btn--on :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
}
.file-item--active :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
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
.preview-bar {
  display: flex;
  align-items: center;
}
.preview-bar > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.doc-handle__split {
  cursor: pointer;
  font-size: 13px;
}
.doc-handle__split:disabled {
  opacity: 0.4;
  cursor: default;
}

/* A2: in-place live-ref badge — a subtopic spawned from this paragraph. Sits at
   the right edge of the doc column, anchored to the paragraph's vertical
   position; scrolls with the content (it lives inside .doc-editor-wrap). */
.doc-liveref {
  position: absolute;
  right: -6px;
  transform: translateY(-2px);
  z-index: 4;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 220px;
  padding: 2px 9px;
  border-radius: 12px;
  font-size: 0.72rem;
  line-height: 1.6;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.35);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s;
}
.doc-liveref:hover {
  background: rgba(var(--v-theme-primary), 0.08);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}
.doc-liveref__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
  background: #f5a623; /* 进行中 default (amber) */
}
.doc-liveref__dot.is-archived,
.doc-liveref__dot.is-completed {
  background: #35b37e; /* 已完成 (green) */
}
.doc-liveref__status {
  color: var(--muted);
  font-size: 0.66rem;
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
