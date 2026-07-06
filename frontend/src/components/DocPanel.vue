<script setup lang="ts">
import { myHandle } from '../me'
import { isPlatformEvent, summarizeActions, toolLabel } from '../lib/toolLabels'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { relTime } from '../lib/relTime'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import { Extension } from '@tiptap/core'
import type { Editor as CoreEditor } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { CellSelection } from '@tiptap/pm/tables'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import type { Node as PMNode } from '@tiptap/pm/model'
import StarterKit from '@tiptap/starter-kit'
import { Markdown } from '@tiptap/markdown'
// Without the table nodes registered, @tiptap/markdown silently DROPS every GFM
// table on parse (the token has no fallback), and a later save would write the
// table-less doc back — data loss, not just a display bug.
import { TableKit } from '@tiptap/extension-table'
import CheeseAvatar from './CheeseAvatar.vue'
import CodeEditor from './CodeEditor.vue'
import {
  BASE as API_BASE,
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
  workspaceFileRawUrl,
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
    // platform: amber dot (cheese action) vs neutral dot (plain work).
    worklog?: { label: string; text: string; platform?: boolean }[]
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
  (e: 'mention-click', handle: string): void
  // 段落评论复用底部主输入框: a selection's 评论 CTA was clicked — the parent
  // flips its composer into comment mode, carrying the anchor + quoted span.
  (e: 'comment-intent', payload: { anchorId: string | null; quote: string }): void
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
// place as a live-ref that shows the subtopic's live status. The badge is a
// ProseMirror WIDGET decoration appended at the end of the upgraded paragraph:
// it lives in the document flow, so it can never float over (and swallow clicks
// meant for) neighbouring text — unlike the old absolutely-positioned overlay
// track, which created cursor dead zones. ---

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

// Top-level doc-node index → the subtopic id that paragraph was upgraded into.
// Refreshed from GET /docs (loadComments); the decoration plugin reads it when
// (re)building. Positional zip: server node i ↔ ProseMirror doc.child(i), same
// alignment contract as alignedDocBlocks.
let liveRefIndex = new Map<number, string>()
const liveRefKey = new PluginKey('cheeseLiveRefBadges')

// Build the badge element a live-ref widget renders as. Title/status are looked
// up from props.topicList at build time; a topicList change rebuilds the set.
function liveRefWidget(topicId: string): HTMLElement {
  const sub = props.topicList.find((t) => t.id === topicId)
  const status = sub?.status ?? ''
  const el = document.createElement('span')
  el.className = 'doc-liveref'
  el.dataset.topic = topicId
  el.contentEditable = 'false'
  el.setAttribute('role', 'button')
  el.title = `子话题「${sub?.title ?? '子话题'}」· ${statusLabel(status)} — 点击打开`
  const dot = document.createElement('span')
  dot.className = `doc-liveref__dot is-${status}`
  const label = document.createElement('span')
  label.className = 'doc-liveref__label'
  label.textContent = `🧩 ${sub?.title ?? '子话题'}`
  const st = document.createElement('span')
  st.className = 'doc-liveref__status'
  st.textContent = statusLabel(status)
  el.append(dot, label, st)
  return el
}

function liveRefDecorations(doc: PMNode): DecorationSet {
  const decos: Decoration[] = []
  doc.forEach((node, offset, index) => {
    const topicId = liveRefIndex.get(index)
    if (!topicId) return
    // End of the block's content (just inside its closing token) — the badge
    // renders after the paragraph's last character, in flow.
    const pos = offset + Math.max(node.nodeSize - 1, 1)
    decos.push(
      Decoration.widget(pos, () => liveRefWidget(topicId), {
        side: 1,
        key: `liveref-${topicId}`,
      }),
    )
  })
  return DecorationSet.create(doc, decos)
}

const LiveRefBadges = Extension.create({
  name: 'cheeseLiveRefBadges',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: liveRefKey,
        state: {
          init: (_cfg, state) => liveRefDecorations(state.doc),
          apply: (tr, old) => {
            // Explicit poke (fresh /docs data or topicList change) → rebuild.
            if (tr.getMeta(liveRefKey)) return liveRefDecorations(tr.doc)
            // Local edits: map the existing widgets along, so a badge stays
            // glued to its paragraph while the user types (indices may shift
            // until the next server refresh; mapping avoids mis-attachment).
            return tr.docChanged ? old.map(tr.mapping, tr.doc) : old
          },
        },
        props: {
          decorations(state) {
            return this.getState(state)
          },
        },
      }),
    ]
  },
})

// Rebuild the index from fresh server nodes and poke the plugin. Nodes align
// positionally with the editor's top-level blocks (trailing tiptap filler
// paragraphs sit past nodes.length and simply never get an entry).
function refreshLiveRefBadges(nodes: Block[]) {
  const next = new Map<number, string>()
  nodes.forEach((n, i) => {
    if (n.upgraded_to_topic_id) next.set(i, n.upgraded_to_topic_id)
  })
  liveRefIndex = next
  const view = editor.value?.view
  if (view) view.dispatch(view.state.tr.setMeta(liveRefKey, true))
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
// refreshComments: the parent composer posts comments in comment mode and asks
// the panel to refresh its lists (drawer + in-doc 常驻评论区).
async function refreshComments() {
  const tid = props.topic?.id
  if (tid) await loadComments(tid).catch(() => {})
}
defineExpose({ pulse, highlightTurn, openFile: openFileRef, refreshComments })

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
  // Same fetch feeds the in-doc live-ref badges (widget decorations).
  refreshLiveRefBadges(ns.data)
}

async function submitComment() {
  const tid = props.topic?.id
  const text = newComment.value.trim()
  if (!tid || !text) return
  commentBusy.value = true
  try {
    await addComment(tid, text, AUTHOR, anchorId.value ?? undefined)
    await loadComments(tid)
    newComment.value = ''
  } catch (e) {
    toolError.value = e instanceof Error ? e.message : '评论失败'
  } finally {
    commentBusy.value = false
  }
}

// 飞书 docs 风常驻评论区: page-level comments (no paragraph anchor) live at the
// bottom of the document itself, with an always-there composer row.
const pageComments = computed(() => comments.value.filter((c) => !c.reply_to))
// 页级评论折叠态 (Feishu-style, collapsed head keeps the doc quiet).
const commentsFolded = ref(false)


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
// 定位: when a file is opened by path (e.g. clicking a <&path> chip in chat or
// the doc), expand every ancestor folder so the tree shows where it lives,
// then scroll the highlighted row into view.
const fileListEl = ref<HTMLElement | null>(null)
function revealInTree(path: string) {
  const parts = path.split('/')
  if (parts.length > 1) {
    const next = new Set(expandedDirs.value)
    let prefix = ''
    for (const part of parts.slice(0, -1)) {
      prefix = prefix ? `${prefix}/${part}` : part
      next.add(prefix)
    }
    expandedDirs.value = next
  }
  void nextTick(() => {
    fileListEl.value
      ?.querySelector('.file-item--active')
      ?.scrollIntoView({ block: 'nearest' })
  })
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
// 全屏预览 (Claude Artifacts style): the same content, workspace-covering.
const previewFull = ref(false)
function openPreviewInNewTab() {
  if (previewAppUrl.value) {
    window.open(previewAppUrl.value, '_blank', 'noopener')
  } else if (previewFile.value && props.topic) {
    // Served with CSP sandbox (opaque origin) — a real tab, not our origin.
    window.open(
      `${API_BASE}/topics/${props.topic.id}/preview/raw`,
      '_blank',
      'noopener',
    )
  }
}

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

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif'])
function isImagePath(path: string): boolean {
  return IMAGE_EXT.has(path.split('.').pop()?.toLowerCase() ?? '')
}
const openIsImage = computed(() => !!openPath.value && isImagePath(openPath.value))
const openImageUrl = computed(() =>
  openPath.value && projectId.value
    ? workspaceFileRawUrl(projectId.value, openPath.value, props.topic?.id)
    : '',
)

async function selectFile(path: string) {
  const pid = projectId.value
  if (!pid) return
  toolError.value = null
  // Images render as images — Monaco would show mangled bytes.
  if (isImagePath(path)) {
    openPath.value = path
    fileDraft.value = ''
    fileSaved.value = ''
    revealInTree(path)
    return
  }
  try {
    const f = await readFile(pid, path, props.topic?.id)
    openPath.value = path
    fileDraft.value = f.content
    fileSaved.value = f.content
    revealInTree(path)
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
// Meta-first rendering: an event block with structured meta ({tool, arg}) is
// translated at DISPLAY time via the full toolLabels table — so a verb missing
// from the table at write time is never frozen untranslated. Rows without meta
// (pre-meta data) fall back to the baked content text.
function eventVerb(b: Block): string {
  if (b.meta?.tool) return toolLabel(b.meta.tool)
  const first = (b.content.split('\n')[0] || '').replace(/^🔧\s*/, '')
  return LEGACY_VERB[first] ?? first
}
function eventArg(b: Block): string {
  if (b.meta?.tool) return b.meta.arg ?? ''
  const nl = b.content.indexOf('\n')
  return nl >= 0 ? b.content.slice(nl + 1).trim() : ''
}
// 圆点分级: amber = platform action, neutral = plain work (structured fields
// only — never guessed from the content text).
function eventPlatform(b: Block): boolean {
  return isPlatformEvent(b.meta, b.refs)
}

const AUTHOR = myHandle()

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

// 结构化 token 装饰 (spec §9.1): decorate our OWN tokens — <@handle> /
// <#topicId> — as clickable chips in the doc, read-only and edit alike.
// Deterministic token parsing, never NL guessing.
const TOKEN_RE = /<@([\w-]+)>|<#([0-9a-fA-F-]{8,})>|<&([\w./\u4e00-\u9fff-]+)>/g

// Build the pretty chip element a token renders as. The raw token stays in the
// document (markdown is the source of truth); the chip is display-only.
function tokenWidget(
  kind: '@' | '#' | '&',
  id: string,
  lookupTopic: (tid: string) => string | undefined,
): HTMLElement {
  const el = document.createElement('span')
  if (kind === '@') {
    el.className = 'mention'
    el.dataset.handle = id
    el.textContent = `@${id}`
  } else if (kind === '#') {
    el.className = 'mention topic-ref'
    el.dataset.topic = id
    el.textContent = `#${lookupTopic(id) ?? '话题'}`
  } else {
    el.className = 'mention file-ref'
    el.dataset.file = id
    el.title = id
    el.textContent = `📄 ${id.split('/').pop() || id}`
  }
  return el
}

function tokenDecorations(
  doc: PMNode,
  lookupTopic: (tid: string) => string | undefined,
): DecorationSet {
  const decos: Decoration[] = []
  doc.descendants((node, pos) => {
    if (!node.isText) return
    const text = node.text ?? ''
    TOKEN_RE.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = TOKEN_RE.exec(text))) {
      const from = pos + m.index
      const to = from + m[0].length
      const kind = m[1] ? '@' : m[2] ? '#' : '&'
      const id = (m[1] ?? m[2] ?? m[3]) as string
      // hide the raw token (inline display:none) + widget(show the chip):
      // the doc keeps `<&path>` verbatim, the reader sees 「📄 name」.
      // (prosemirror-view has no Decoration.replace — widget/inline/node only.)
      decos.push(
        Decoration.widget(from, () => tokenWidget(kind, id, lookupTopic), {
          side: 1,
        }),
        Decoration.inline(from, to, { style: 'display: none' }),
      )
    }
  })
  return DecorationSet.create(doc, decos)
}

function lookupTopicTitle(tid: string): string | undefined {
  return props.topicList.find((t) => t.id === tid)?.title
}

const TokenChips = Extension.create({
  name: 'cheeseTokenChips',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        state: {
          init: (_cfg, state) => tokenDecorations(state.doc, lookupTopicTitle),
          apply: (tr, old) =>
            tr.docChanged ? tokenDecorations(tr.doc, lookupTopicTitle) : old,
        },
        props: {
          decorations(state) {
            return this.getState(state)
          },
        },
      }),
    ]
  },
})

// Chip clicks in the doc (delegated — decorations are plain spans).
function onDocClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  // Live-ref badge widget → open its subtopic.
  const lr = target?.closest('.doc-liveref') as HTMLElement | null
  if (lr?.dataset.topic) {
    emit('open-topic', lr.dataset.topic)
    return
  }
  const el = target?.closest('.mention') as HTMLElement | null
  if (!el) return
  if (el.dataset.topic) emit('open-topic', el.dataset.topic)
  else if (el.dataset.handle) emit('mention-click', el.dataset.handle)
  else if (el.dataset.file) void openFileRef(el.dataset.file)
}

// A <&path> chip opens that file in the 文件 drawer's editor.
async function openFileRef(path: string) {
  openTool.value = 'files'
  drawerOpen.value = true
  await loadTool('files')
  await selectFile(path)
}

const editor = useEditor({
  content: '',
  extensions: [
    StarterKit,
    Markdown,
    TableKit.configure({ table: { resizable: false } }),
    TokenChips,
    LiveRefBadges,
  ],
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
    queueAutosave()
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
  const sel = ed.state.selection
  if (sel.empty || !editable.value) {
    commentCta.value = null
    return
  }
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  if (!wrap) return
  let quote: string
  let startCoords: { top: number; left: number }
  let endRight: number
  let nodeIndex: number
  try {
    if (sel instanceof CellSelection) {
      // TableKit: dragging across cells yields a CellSelection, not a
      // TextSelection. Its from/to are cell-boundary positions — textBetween
      // and coordsAtPos on them give garbage (empty quote / border coords),
      // which is what broke commenting inside tables. Read the selected CELLS
      // instead: quote = their text, coords = the anchor/head cells' insides,
      // and the paragraph anchor = the table's own top-level node index.
      const parts: string[] = []
      sel.forEachCell((cell) => {
        const t = cell.textContent.trim()
        if (t) parts.push(t)
      })
      quote = parts.join(' ')
      const a = ed.view.coordsAtPos(sel.$anchorCell.pos + 1)
      const h = ed.view.coordsAtPos(sel.$headCell.pos + 1)
      startCoords = a.top < h.top || (a.top === h.top && a.left <= h.left) ? a : h
      endRight = Math.max(a.right, h.right)
      nodeIndex = sel.$anchorCell.index(0)
    } else {
      quote = ed.state.doc.textBetween(sel.from, sel.to, ' ').trim()
      startCoords = ed.view.coordsAtPos(sel.from)
      endRight = ed.view.coordsAtPos(sel.to).right
      // depth-0 index = the top-level block the selection starts in (inside a
      // table cell this still resolves to the table's index — correct anchor).
      nodeIndex = sel.$from.index(0)
    }
  } catch {
    // coordsAtPos can throw on transient positions mid-edit; just hide the CTA.
    commentCta.value = null
    return
  }
  if (!quote) {
    commentCta.value = null
    return
  }
  const wrapRect = wrap.getBoundingClientRect()
  commentCta.value = {
    top: startCoords.top - wrapRect.top - 38,
    left: Math.min(endRight, startCoords.left + 240) - wrapRect.left,
    quote,
    nodeIndex,
  }
}

async function commentOnSelection() {
  const ed = editor.value
  const tid = props.topic?.id
  const cta = commentCta.value
  if (!ed || !tid || !cta) return
  const nodes = (await getDocNodes(tid)).data
  // Same filler-tolerant alignment as split/highlight. Falls back to a
  // whole-doc comment if the structure can't be mapped.
  const anchor =
    cta.nodeIndex >= nodes.length || nodes.length !== contentBlocks().length
      ? null
      : nodes[cta.nodeIndex].id
  commentCta.value = null
  // 复用底部主输入框: hand the anchor + quote to the parent composer, which
  // flips into comment mode (quote chip + Esc/✕ to exit).
  emit('comment-intent', { anchorId: anchor, quote: cta.quote })
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

// The panel already renders the topic title as the page title (Feishu Docs).
// A doc whose first line is an H1 EXACTLY equal to that title would show it
// twice — drop that one line (pure string equality, no guessing).
function stripDuplicateTitle(md: string): string {
  const title = props.topic?.title?.trim()
  if (!title) return md
  const m = md.match(/^#\s+(.+?)\s*\n+/)
  return m && m[1].trim() === title ? md.slice(m[0].length) : md
}

async function loadDoc(topicId: string) {
  errorMsg.value = null
  loading.value = true
  try {
    const block = await getDoc(topicId)
    // Avoid races on fast topic switching.
    if (props.topic?.id !== topicId) return
    const md = stripDuplicateTitle(block?.content ?? '')
    lastSavedMarkdown.value = md
    setEditorMarkdown(md)
    dirty.value = false
    savedAt.value = null
    // A2 badges + 常驻评论区: refresh nodes/comments for the new doc.
    void loadComments(topicId).catch(() => {})
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
    const md = stripDuplicateTitle(block?.content ?? '')
    if (md !== lastSavedMarkdown.value) {
      lastSavedMarkdown.value = md
      setEditorMarkdown(md)
      savedAt.value = null
    }
    // A2 badges + 常驻评论区: refresh alongside the doc content.
    void loadComments(topicId).catch(() => {})
  } catch {
    // Silent: activity-driven refresh is best-effort.
  }
}

// Feishu-style autosave: an explicit 保存 button reads as unfinished software.
// Debounced from the LAST keystroke (not the dirty flip, which only fires
// once per dirty cycle); ⌘S still saves immediately.
let autosaveTimer: ReturnType<typeof setTimeout> | null = null
function queueAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(() => {
    if (dirty.value && editable.value && !saving.value) void save()
  }, 2500)
}

function onDocKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    if (dirty.value && editable.value) void save()
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
// was spawned), refetch the doc nodes and rebuild the badge widgets so titles /
// status stay live. (No resize listener needed anymore — widgets are in flow.)
watch(
  () => props.topicList,
  () => {
    const tid = props.topic?.id
    if (tid) void loadComments(tid).catch(() => {})
  },
  { deep: true },
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
        <span v-else-if="dirty" class="t-meta me-2">编辑中…</span>

        <v-btn size="small" variant="text" class="me-1 c-muted" @click="toggleEditable">
          {{ editable ? '只读' : '编辑' }}
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
          <div class="doc-editor-wrap" @click="onDocClick" @keydown="onDocKeydown">
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
            <!-- A2 in-place live-refs are ProseMirror widget decorations now —
                 rendered in the document flow at the end of their paragraph by
                 the LiveRefBadges extension (no overlay, no cursor dead zone).
                 Clicks are delegated through onDocClick above. -->
            <!-- Real block handles: 🧩 splits the block into a subtopic, ⠿ drags to
                 reorder, ＋ inserts a block below. Only in edit mode. -->
            <DragHandle
              v-if="editor && editable"
              :editor="editor"
              :on-node-change="onDocNodeChange"
              class="doc-handle"
            >
              <!-- mdi icons, not text glyphs: "+" (18px font) and "⠿"
                   (braille, 16px) center on different baselines and read as
                   non-parallel; icons share one geometric grid. -->
              <button
                type="button"
                class="doc-handle__btn doc-handle__add"
                title="在下方插入块"
                draggable="false"
                @dragstart.stop.prevent
                @click="addBlockBelow"
              >
                <v-icon size="15">mdi-plus</v-icon>
              </button>
              <span class="doc-handle__btn doc-handle__grip" title="拖动以排序">
                <v-icon size="15">mdi-drag-vertical</v-icon>
              </span>
            </DragHandle>

          </div>

          <!-- 飞书 docs 风常驻评论区: page-level comments (no paragraph anchor)
               live at the bottom of the document, with an always-there
               "写评论…" row. Paragraph-anchored comments stay in the drawer. -->
          <div class="doc-comments">
            <!-- Collapsible head; ONE 写评论 action that reuses the main
                 composer in comment mode — the doc never grows its own input. -->
            <div class="doc-comments__head">
              <button
                type="button"
                class="doc-comments__fold"
                :title="commentsFolded ? '展开评论' : '收起评论'"
                @click="commentsFolded = !commentsFolded"
              >
                <v-icon size="15" class="c-faint">
                  {{ commentsFolded ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
                </v-icon>
                <v-icon size="15" class="c-faint">mdi-comment-text-outline</v-icon>
                评论
                <span v-if="pageComments.length" class="doc-comments__count">
                  {{ pageComments.length }}
                </span>
              </button>
              <v-spacer />
              <v-btn
                icon="mdi-plus"
                size="x-small"
                variant="tonal"
                color="primary"
                title="写评论"
                @click="emit('comment-intent', { anchorId: null, quote: '' })"
              />
            </div>
            <template v-if="!commentsFolded">
              <div v-for="c in pageComments" :key="c.id" class="doc-comments__item">
                <span class="doc-comments__avatar">
                  {{ (c.author || '?').slice(0, 1).toUpperCase() }}
                </span>
                <div class="doc-comments__main">
                  <div class="doc-comments__meta">
                    <span class="doc-comments__author">{{ c.author }}</span>
                    <span class="t-meta">{{ relTime(c.created_at) }}</span>
                  </div>
                  <div class="doc-comments__text">{{ c.content }}</div>
                </div>
              </div>
            </template>
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
            <template v-if="openTool === 'preview' && (previewAppUrl || previewFile)">
              <v-btn
                icon="mdi-open-in-new"
                size="small"
                variant="text"
                class="c-muted"
                title="在新标签页打开"
                @click="openPreviewInNewTab"
              />
              <v-btn
                icon="mdi-arrow-expand-all"
                size="small"
                variant="text"
                class="c-muted"
                title="全屏预览"
                @click="previewFull = true"
              />
            </template>
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
                  <span
                    class="site-act__dot"
                    :class="{ 'site-act__dot--platform': eventPlatform(b) }"
                  >●</span>
                  <div class="site-act__body">
                    <span class="site-act__verb">{{ eventVerb(b) }}</span>
                    <div v-if="eventArg(b)" class="site-act__arg">
                      ⎿ {{ eventArg(b) }}
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
                      'site-act__dot--platform': act.platform,
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
                <div v-if="fileListOpen" ref="fileListEl" class="file-list">
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
                  <div v-if="openPath && openIsImage" class="file-image-view">
                    <img :src="openImageUrl" :alt="openPath" />
                  </div>
                  <CodeEditor
                    v-else-if="openPath"
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
                <div class="d-flex align-center mb-1">
                  <span class="text-caption font-weight-medium">{{ c.author }}</span>
                  <span class="text-caption c-faint ms-2">{{
                    relTime(c.created_at)
                  }}</span>
                </div>
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
              <!-- anchor picker: whole-doc / paragraph comments made without a
                   text selection (selection comments go through the main
                   composer's comment mode instead). -->
              <v-select
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
                v-model="newComment"
                placeholder="写条评论…"
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

      <!-- 全屏预览 overlay: same artifact, workspace-covering (Esc / ✕ closes). -->
      <Teleport to="body">
        <div v-if="previewFull" class="preview-full" @keydown.esc="previewFull = false">
          <div class="preview-full__bar">
            <span class="preview-full__title">
              {{ previewAppUrl ? previewAppNote || '运行中的应用' : previewFile?.path }}
            </span>
            <v-spacer />
            <v-btn
              icon="mdi-open-in-new"
              size="small"
              variant="text"
              class="c-muted"
              title="在新标签页打开"
              @click="openPreviewInNewTab"
            />
            <v-btn
              icon="mdi-close"
              size="small"
              variant="text"
              class="c-muted"
              @click="previewFull = false"
            />
          </div>
          <iframe
            v-if="previewAppUrl"
            class="preview-full__frame"
            :src="previewAppUrl"
            sandbox="allow-same-origin allow-scripts allow-forms"
          />
          <iframe
            v-else-if="previewFile"
            class="preview-full__frame"
            :srcdoc="previewFile.content"
            sandbox="allow-scripts"
          />
        </div>
      </Teleport>

      <!-- Floating, never clipped: the old flow-layout alert sat below the
           scroll stage and rendered half-hidden at the panel edge. -->
      <v-alert
        v-if="errorMsg"
        type="error"
        density="compact"
        class="doc-error-toast"
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
  color: rgb(var(--v-theme-primary)) !important;
  background: transparent;
}
.doc-editor-wrap {
  position: relative;
}
/* Placeholder as ::before INSIDE the empty first paragraph: the ghost text
   shares the paragraph's exact font metrics, so the caret sits cleanly at
   its left edge instead of cutting through a misaligned overlay. PM renders
   an empty doc as <p><br class="ProseMirror-trailingBreak"></p>. */
.doc-editor :deep(.doc-prose > p:first-child:last-child:has(> br.ProseMirror-trailingBreak:only-child))::before {
  content: '芝士会在这里维护文档，你也可以直接编辑';
  color: rgba(var(--v-theme-on-surface), 0.38);
  pointer-events: none;
  float: left;
  height: 0;
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
/* 评论卡片 (drawer): surface card with a hairline border + soft shadow, matching
   the in-doc 常驻评论区 cards. */
.comment-item {
  background: var(--surface);
  border: 1px solid var(--line-2, #ececec);
  border-radius: 10px;
  padding: 10px 12px;
  box-shadow: 0 1px 3px rgba(16, 18, 22, 0.04);
}
.comment-item .comment-quote {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  border-left: 2px solid var(--accent, #f57f17);
  background: rgba(245, 127, 23, 0.06);
  border-radius: 0 6px 6px 0;
  padding: 4px 8px;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 文档里的 @/话题 chip：和聊天同一视觉词汇，可点。 */
.doc-editor :deep(.mention) {
  color: rgb(var(--v-theme-primary));
  background: var(--fill);
  border-radius: 4px;
  padding: 0 3px;
  font-weight: 500;
  cursor: pointer;
}
.doc-editor :deep(.mention:hover) {
  text-decoration: underline;
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
  height: 44px;
  padding: 0 8px 0 16px;
  flex: 0 0 auto;
  background: var(--surface);
}
.tool-panel__title {
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--ink);
}
/* Quiet the head's icon buttons until hovered (图钉/关闭 shouldn't shout). */
.tool-panel__head .v-btn {
  opacity: 0.75;
  transition: opacity 0.12s ease;
}
.tool-panel__head .v-btn:hover,
.tool-panel__head .tool-btn--active {
  opacity: 1;
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
@keyframes site-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
@media (prefers-reduced-motion: reduce) {
  .site-act__dot--live { animation: none; }
}
/* 圆点分级: neutral = plain work (read/search/run), amber = platform action
   (cheese tool / cheese CLI / doc edit). --live (pulse) overrides both. */
.site-act__dot {
  color: var(--faint);
  flex: 0 0 auto;
}
.site-act__dot--platform {
  color: var(--accent);
}
/* Declared last so the live pulse wins over both dot tiers. */
.site-act__dot--live {
  color: rgb(var(--v-theme-primary));
  animation: site-pulse 1.2s ease-in-out infinite;
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

/* GFM tables (TableKit). tiptap emits div.tableWrapper > table; browsers give
   tables no borders by default, so without this the table renders "naked". */
.doc-editor :deep(.doc-prose .tableWrapper) {
  overflow-x: auto;
  margin: 12px 0;
}
.doc-editor :deep(.doc-prose table) {
  border-collapse: collapse;
  width: 100%;
  font-size: 14px;
}
.doc-editor :deep(.doc-prose th),
.doc-editor :deep(.doc-prose td) {
  border: 1px solid var(--line, #dcdfe6);
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
  /* anchor for the .selectedCell::after overlay */
  position: relative;
}
/* CellSelection feedback: prosemirror-tables marks selected cells with
   .selectedCell but ships no styling — without this, dragging across cells
   looked like the selection was lost (it wasn't). */
.doc-editor :deep(.doc-prose .selectedCell::after) {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 2;
  pointer-events: none;
  background: rgba(var(--v-theme-primary), 0.1);
}
.doc-editor :deep(.doc-prose th) {
  background: var(--bg-2, #f7f8fa);
  font-weight: 600;
}

/* Feishu-style left gutter block handles — REAL controls, not decoration.
   The DragHandle floats next to the hovered block (positioned by the extension).
   ＋ inserts a block below (click); ⠿ drags to reorder. */
.doc-handle {
  display: flex;
  align-items: center;
  gap: 2px;
  /* The DragHandle plugin pins this element's RIGHT edge to the text's left
     edge — without the padding the ⠿ glyph literally touches the first
     character. The padding is the breathing room (Feishu keeps ~14px). */
  padding-right: 14px;
  /* Nudge down so the 22px buttons center on the ~29px first text line. */
  transform: translateY(3.4px);
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
}
.doc-handle__split {
  cursor: pointer;
  font-size: 13px;
}
.doc-handle__split:disabled {
  opacity: 0.4;
  cursor: default;
}

.preview-full {
  position: fixed;
  inset: 0;
  z-index: 2400;
  display: flex;
  flex-direction: column;
  background: var(--surface, #fff);
}
.preview-full__bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--line-2, #e5e5e5);
}
.preview-full__title {
  font-size: 0.85rem;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-full__frame {
  flex: 1;
  border: 0;
  width: 100%;
}

.file-image-view {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  overflow: auto;
  background:
    conic-gradient(var(--line-2) 0 25%, transparent 0 50%, var(--line-2) 0 75%, transparent 0)
    0 0 / 16px 16px; /* checkerboard so transparency reads */
}
.file-image-view img {
  max-width: 95%;
  max-height: 95%;
  object-fit: contain;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.15);
  background: white;
}

.doc-error-toast {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  z-index: 30;
  max-width: min(560px, calc(100% - 32px));
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
}

/* A2: in-place live-ref badge — a subtopic spawned from this paragraph. It's a
   ProseMirror widget decoration rendered IN the document flow, right after the
   paragraph's last character — no overlay, so it can never block the caret.
   :deep because the widget span is created imperatively by the extension. */
.doc-editor :deep(.doc-liveref) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  vertical-align: baseline;
  margin-left: 8px;
  max-width: 240px;
  padding: 1px 9px;
  border-radius: 10px;
  font-size: 0.72rem;
  line-height: 1.6;
  white-space: nowrap;
  color: rgb(var(--v-theme-primary));
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 5%, var(--surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  box-shadow: 0 1px 3px rgba(16, 18, 22, 0.06);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s, box-shadow 0.15s;
}
.doc-editor :deep(.doc-liveref:hover) {
  background: rgba(var(--v-theme-primary), 0.1);
  box-shadow: 0 2px 8px rgba(16, 18, 22, 0.1);
}
.doc-editor :deep(.doc-liveref__label) {
  overflow: hidden;
  text-overflow: ellipsis;
}
.doc-editor :deep(.doc-liveref__dot) {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
  background: #f5a623; /* 进行中 default (amber) */
}
.doc-editor :deep(.doc-liveref__dot.is-archived),
.doc-editor :deep(.doc-liveref__dot.is-completed) {
  background: #35b37e; /* 已完成 (green) */
}
.doc-editor :deep(.doc-liveref__status) {
  color: var(--muted);
  font-size: 0.66rem;
}

/* 飞书 docs 风常驻评论区 at the bottom of the document column. */
.doc-comments {
  max-width: 720px;
  margin: 40px auto 0;
  padding-top: 14px;
  border-top: 1px solid var(--line-2, #ececec);
}
.doc-comments__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 12px;
}
.doc-comments__count {
  font-size: 11px;
  font-weight: 600;
  padding: 0 6px;
  border-radius: 8px;
  color: var(--muted);
  background: var(--fill);
}
.doc-comments__item {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--line-2, #ececec);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: 0 1px 3px rgba(16, 18, 22, 0.04);
  margin-bottom: 8px;
}
.doc-comments__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  flex: 0 0 auto;
  font-size: 0.7rem;
  font-weight: 700;
  color: #fff;
  background: #8a94a3;
}
.doc-comments__main {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-comments__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 1px;
}
.doc-comments__author {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
}
.doc-comments__text {
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
.doc-comments__composer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.doc-comments__input {
  flex: 1 1 auto;
  min-width: 0;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--line-2, #ececec);
  border-radius: 8px;
  background: var(--fill);
  font-size: 13.5px;
  color: var(--text);
  outline: none;
  transition: border-color 0.15s, background 0.15s;
}
.doc-comments__input:focus {
  border-color: rgba(var(--v-theme-primary), 0.5);
  background: var(--surface);
}
.doc-comments__input::placeholder {
  color: var(--faint);
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
