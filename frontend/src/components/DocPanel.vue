<script setup lang="ts">
import type { ChainedCommands, Editor as CoreEditor } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { SuggestionProps } from '@tiptap/suggestion'
import type { Block, FileContent, GitCommit, PreviewInfo, Topic, UsageStats, WorkspaceFile } from '../cx_types'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Extension } from '@tiptap/core'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { CellSelection } from '@tiptap/pm/tables'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import { Suggestion } from '@tiptap/suggestion'
import { EditorContent, useEditor } from '@tiptap/vue-3'

import {
  ApiError,
  BASE as API_BASE,
  getComments,
  getDoc,
  getDocNodes,
  getGitDiff,
  getGitLog,
  getPreview,
  getProjectUsage,
  getTerminal,
  getTopicUsage,
  getTranscript,
  listFiles,
  primeAppPreview,
  putDoc,
  readFile,
  withSessionToken,
  workspaceFileRawUrl,
  writeFile,
} from '../api'
// The editor schema + round-trip fidelity machinery live in docMarkdown.ts —
// ONE extension list shared with the corpus tests, so "what the tests prove"
// and "what the editor runs" can never drift apart. (History: TipTap without
// the table extension silently DROPPED every GFM table on parse, and a later
// save wrote the table-less doc back — data loss. 军规 1.)
import {
  autosavePaused,
  docSaveStatus,
  dropStash,
  planExternalUpdate,
  planSourceModeEntry,
  popStash,
  pushStash,
} from '../lib/docEditState'
import { compareRoundTrip, docExtensions, serializeDoc } from '../lib/docMarkdown'
import { relTime } from '../lib/relTime'
import { countLines, isLongSiteEntry, shouldFollowTail, shouldKeepPinning, SITE_CLAMP_LINES } from '../lib/siteLog'
import { isPlatformEvent, summarizeActions, toolLabel } from '../lib/toolLabels'
import { costLabel, costNote, fmtNum } from '../lib/usageFormat'
import { myHandle } from '../me'

import CheeseAvatar from './CheeseAvatar.vue'
import CodeEditor from './CodeEditor.vue'

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
  }
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
  const els = Array.from(document.querySelectorAll('.doc-editor .ProseMirror > *')) as HTMLElement[]
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
      })
    )
  })
  return DecorationSet.create(doc, decos)
}

// ---- 评论下划线 (Feishu): each anchored comment's quote gets a clickable
// dashed underline in the doc. Deterministic: the stored quote is OUR
// structured field; we locate it by exact substring inside its anchored
// block (positional node↔block alignment, same as live-refs). No match →
// no mark (the paragraph changed; the bottom card already says so). ----
let commentMarkIndex = new Map<number, { id: string; quote: string }[]>()
const commentMarkKey = new PluginKey('cheeseCommentMarks')

function commentMarkDecorations(doc: PMNode): DecorationSet {
  const decos: Decoration[] = []
  doc.forEach((node, offset, index) => {
    const anchored = commentMarkIndex.get(index)
    if (!anchored?.length) return
    const text = node.textContent
    for (const c of anchored) {
      const at = text.indexOf(c.quote)
      if (at < 0) continue
      // +1: past the block's opening token into its text content.
      decos.push(
        Decoration.inline(offset + 1 + at, offset + 1 + at + c.quote.length, {
          class: 'comment-anchor',
          'data-comment': c.id,
        })
      )
    }
  })
  return DecorationSet.create(doc, decos)
}

const CommentMarks = Extension.create({
  name: 'cheeseCommentMarks',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: commentMarkKey,
        state: {
          init: (_cfg, state) => commentMarkDecorations(state.doc),
          apply: (tr, old) => {
            if (tr.getMeta(commentMarkKey)) return commentMarkDecorations(tr.doc)
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
const toolWidth = ref<number>(Number(localStorage.getItem('cheesex.toolWidth')) || 380)
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
const activeToolLabel = () => TOOLS.find((t) => t.key === openTool.value)?.label ?? ''

const projectId = computed<string | null>(() => props.topic?.project_id ?? null)

// ---- Per-tool data (lazy-loaded when its drawer opens) ----
const toolLoading = ref(false)
// A background re-fetch: spins only the 刷新 button, never replaces the panel.
const toolRefreshing = ref(false)
const toolError = ref<string | null>(null)

// 评论 (B4): inline comments anchored to doc nodes. anchorNodes lists the doc's
// paragraphs so an anchored comment (reply_to = node id) can be located and
// flashed; comments without an anchor are page-level.
const comments = ref<Block[]>([])
const anchorNodes = ref<Block[]>([])

// A short label for a doc node, used as the anchor-chip fallback when a comment
// has no quoted span. The node's own content is either AI- or human-authored
// text; we only ever truncate it for display (never to derive semantics).
function nodeLabel(content: string): string {
  const t = content.replace(/^#+\s*/, '').trim()
  return t.length > 22 ? t.slice(0, 22) + '…' : t || '(空段落)'
}
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
  // …and the comment underlines: node id → top-level index, then group the
  // anchored comments (with usable quotes) under their block's index.
  const idToIndex = new Map(ns.data.map((n, i) => [n.id, i]))
  const nextMarks = new Map<number, { id: string; quote: string }[]>()
  for (const c of cs.data) {
    const anchorId = c.reply_to
    const quote = (c.anchor_quote || '').trim()
    if (!anchorId || !quote) continue
    const idx = idToIndex.get(anchorId)
    if (idx === undefined) continue
    const list = nextMarks.get(idx) ?? []
    list.push({ id: c.id, quote })
    nextMarks.set(idx, list)
  }
  commentMarkIndex = nextMarks
  const cmView = editor.value?.view
  if (cmView) cmView.dispatch(cmView.state.tr.setMeta(commentMarkKey, true))
}

// 飞书 docs 风常驻评论区: ALL comments live at the bottom of the document —
// anchored ones carry a quote chip that scrolls/flashes their paragraph;
// page-level ones render plain. (评论归一: the old drawer tool is gone.)
// 页级评论折叠态 (Feishu-style, collapsed head keeps the doc quiet).
const commentsFolded = ref(false)

// 现场: read-only transcript timeline.
const transcript = ref<Block[]>([])
// The 现场 scroll container, so the timeline can open on its newest entry the
// way a chat log does. Measured before this existed: opening 现场 left
// scrollTop at 0 with a scrollHeight of 1818 and a viewport of 500 — the
// reader landed 1300px above the thing they came to see.
const toolContentRef = ref<HTMLElement | null>(null)
// Entries the reader has expanded. Keyed by block id, and deliberately NOT
// reset when the transcript refreshes: a silent refresh re-collapsing what
// someone just opened is the same bug as scrolling them away from it.
const expandedSite = ref<Set<string>>(new Set())

function toggleSiteEntry(id: string): void {
  const next = new Set(expandedSite.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedSite.value = next
}

// A single `scrollTop = scrollHeight` at nextTick does NOT work here, which is
// how this shipped broken the first time: the panel renders its spinner first,
// so the container is one viewport tall with nothing to scroll, the assignment
// clamps to 0, and the timeline lays out underneath — leaving the reader on the
// oldest entry, the exact bug this exists to fix. Measured on the deployed page:
// scrollHeight 500 at +40ms, 2066 at +120ms, scrollTop 0 throughout.
// So keep re-pinning while the height is still moving (see shouldKeepPinning).
function scrollSiteToTail(): void {
  let lastHeight = -1
  let frames = 0
  const pin = (): void => {
    const el = toolContentRef.value
    if (!el) return
    el.scrollTop = el.scrollHeight
    if (shouldKeepPinning(el.scrollHeight, lastHeight, frames)) {
      lastHeight = el.scrollHeight
      frames += 1
      requestAnimationFrame(pin)
    }
  }
  nextTick(() => requestAnimationFrame(pin))
}

// 本轮实时动作 appends to the bottom of the same list while a turn runs, so it
// has to follow the tail too — otherwise 现场 opens on the newest entry and then
// grows out of view while you watch it. Only when the reader is already parked
// at the bottom: someone who scrolled up to read a tool argument is reading it.
watch(
  () => props.worklog.length,
  () => {
    const el = toolContentRef.value
    if (openTool.value === 'site' && el && shouldFollowTail(el)) scrollSiteToTail()
  }
)
// 现场实时终端: when the tmux backend has this topic's container up, the 现场
// drawer embeds the real read-only terminal (ttyd) instead of the rebuilt
// worklog. `terminalUrl` is the backend proxy the iframe loads.
const terminalUrl = ref<string | null>(null)
// Live-turn elapsed seconds (ticks while `working`).
const nowTick = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null
watch(
  () => props.working,
  (w) => {
    if (tickTimer) clearInterval(tickTimer)
    tickTimer = w ? setInterval(() => (nowTick.value = Date.now()), 1000) : null
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  if (tickTimer) clearInterval(tickTimer)
})
const liveElapsed = computed(() => {
  if (!props.working || !props.workingSince) return null
  return Math.max(0, Math.round((nowTick.value - props.workingSince) / 1000))
})
const liveSummary = computed(() => summarizeActions(props.worklog.map((w) => w.label)))
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
// Version of the open file as it was read; echoed back on save so a write that
// lost a race to 芝士 is rejected instead of silently erasing their edits.
const fileVersion = ref<string | null>(null)
// Files that must not be edited as text: binary (a text round-trip destroys
// them) or too large to send. They open read-only, with no 保存 button.
const fileBinary = ref(false)
const fileTooLarge = ref(false)
const fileBytes = ref(0)
const fileReadOnly = computed(() => fileBinary.value || fileTooLarge.value || openIsImage.value)
// Set when the backend rejected a save as a conflict. Nobody wins by default —
// the human sees it and picks.
const fileConflict = ref(false)

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
    fileListEl.value?.querySelector('.file-item--active')?.scrollIntoView({ block: 'nearest' })
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
// 运行环境预览: the agent declared a RUNNING app (cheese serve) — iframe the
// backend's reverse-proxy path for its container instead of rendering file
// content. Null while the app isn't answering; `previewContainerUp` then says
// whether the box is even there, so the two cases can read differently.
const previewAppUrl = ref<string | null>(null)
const previewAppNote = ref<string>('')
const previewContainerUp = ref(false)
// What 芝士 named, app or file — so a read failure can say WHICH artifact broke.
const previewNamedPath = ref<string>('')
// Failures, kept apart from "nothing is set". Collapsing them (the old
// `.catch(() => null)` on both calls) reported every backend error and every
// unreadable file as "芝士还没有指定预览" — a broken panel that looked idle, so
// nobody reported it.
const previewError = ref<string | null>(null)
const previewReadError = ref<string | null>(null)
// 全屏预览 (Claude Artifacts style): the same content, workspace-covering.
const previewFull = ref(false)
// Esc closes it. The overlay div carried a `@keydown.esc`, but a plain div is
// never focused, so the handler could not fire and the ✕ was the only way out.
// A window listener, mounted only while the overlay is up, actually gets the key.
function onPreviewFullKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') previewFull.value = false
}
watch(previewFull, (open) => {
  if (open) window.addEventListener('keydown', onPreviewFullKeydown)
  else window.removeEventListener('keydown', onPreviewFullKeydown)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onPreviewFullKeydown))
function openPreviewInNewTab() {
  if (previewAppUrl.value) {
    window.open(previewAppUrl.value, '_blank', 'noopener')
  } else if (previewFile.value && props.topic) {
    // Served with CSP sandbox (opaque origin) — a real tab, not our origin.
    window.open(`${API_BASE}/topics/${props.topic.id}/preview/raw`, '_blank', 'noopener')
  }
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// 资源面板的数字格式 (lib/usageFormat): grouping, magnitude-aware precision, and
// the 未知-not-zero rule for subscription usage that carries no per-token price.

// `silent`: a background re-fetch (auto-refresh / the 刷新 button). It must not
// blank the panel behind a spinner — the point is that what you are reading
// gets newer, not that it disappears and comes back.
async function loadTool(key: string, opts: { silent?: boolean } = {}) {
  const tid = props.topic?.id
  const pid = projectId.value
  if (!tid || !pid) return
  if (opts.silent) toolRefreshing.value = true
  else toolLoading.value = true
  toolError.value = null
  try {
    if (key === 'site') {
      // Prefer the real terminal (tmux backend); fall back to the worklog
      // timeline. The terminal probe must never break 现场 — on any error it
      // just stays null and the worklog view renders.
      const [tx, term] = await Promise.all([getTranscript(tid), getTerminal(tid).catch(() => null)])
      if (props.topic?.id !== tid) return
      // Follow the tail on the FIRST load of a topic's 现场 unconditionally
      // (that is what "open on the newest" means), and on a silent refresh only
      // when the reader is still parked at the bottom.
      const follow = !opts.silent || !toolContentRef.value || shouldFollowTail(toolContentRef.value)
      transcript.value = tx.data
      if (follow) scrollSiteToTail()
      // `url` is a root-relative path ("/api/topics/…/terminal/live/") loaded
      // through the same dev/proxy that fronts /api. The session token has to be
      // appended: the proxy authorizes every request and an iframe can carry no
      // header, so the bare URL 404s and the drawer renders a white box that
      // never falls back. `available` is the backend's own probe (credential +
      // container + something actually answering), so a false here means the
      // timeline below is the honest thing to show.
      terminalUrl.value = term?.available && term.url ? withSessionToken(term.url) : null
    } else if (key === 'git') {
      // A fresh repo with no commits makes git log fail (422); tolerate it so
      // the diff still renders instead of the whole drawer showing an error.
      // Always topic-scoped: the project-level answer is OTHER topics' commits
      // (before 采纳 this topic's commits live only on its branch; after, the
      // base is everyone's).
      const [log, diff] = await Promise.all([
        getGitLog(pid, tid).catch(() => ({ data: [] as GitCommit[], total: 0 })),
        getGitDiff(pid, tid),
      ])
      // Guard against a topic switch mid-flight.
      if (props.topic?.id !== tid) return
      gitCommits.value = log.data
      gitDiff.value = diff.diff
    } else if (key === 'files') {
      const listed = (await listFiles(pid, tid)).data
      // Guard against a topic switch mid-flight, like every other tool does —
      // without it the previous topic's listing repopulates the new panel.
      if (props.topic?.id !== tid) return
      files.value = listed
      // Keep the open file if it still exists; otherwise open the first file.
      if (!openPath.value || !files.value.some((f) => f.path === openPath.value)) {
        openPath.value = null
        if (files.value.length) await selectFile(files.value[0].path)
      }
    } else if (key === 'resources') {
      const [tu, pu] = await Promise.all([getTopicUsage(tid), getProjectUsage(pid)])
      if (props.topic?.id !== tid) return
      topicUsage.value = tu
      projectUsage.value = pu
    } else if (key === 'preview') {
      // 芝士 points at the current preview via `cheese artifact` (render-by-type,
      // spec §7.1/§9.1). NO guessing when it hasn't named one: the old
      // first-*.html fallback proudly served frontend/index.html — an SPA
      // shell that renders blank — which is exactly why the spec says the
      // platform never picks the preview itself.
      previewAppUrl.value = null
      previewAppNote.value = ''
      previewContainerUp.value = false
      previewError.value = null
      previewReadError.value = null
      previewNamedPath.value = ''
      let art: PreviewInfo | null
      try {
        art = await getPreview(tid)
      } catch (e) {
        if (props.topic?.id !== tid) return
        // "The backend errored" is its own state — not "nothing is set".
        previewNamed.value = false
        previewFile.value = null
        previewError.value = e instanceof Error ? e.message : '加载失败'
        return
      }
      if (props.topic?.id !== tid) return
      if (art && art.kind === 'app') {
        previewNamed.value = true
        previewAppNote.value = art.path
        previewNamedPath.value = art.path
        previewContainerUp.value = !!art.container_up
        previewFile.value = null
        if (art.url) {
          // The frame carries no credential of its own (a ?token= would be
          // readable by whatever the agent is serving), so hand the browser the
          // scoped cookie FIRST — otherwise its very first request 404s and the
          // panel is back to showing a white box.
          try {
            await primeAppPreview(tid)
          } catch (e) {
            if (props.topic?.id !== tid) return
            previewError.value = e instanceof Error ? e.message : '预览授权失败'
            return
          }
          if (props.topic?.id !== tid) return
        }
        previewAppUrl.value = art.url ?? null
      } else if (art) {
        previewNamed.value = true
        previewNamedPath.value = art.path
        previewMime.value = art.mime || 'text/html'
        try {
          const content = await readFile(pid, art.path, tid)
          // Guard against a topic switch mid-flight — this await was the one
          // fetch in the drawer without it, so a slow read could paint topic A's
          // artifact into topic B's panel.
          if (props.topic?.id !== tid) return
          previewFile.value = content
        } catch (e) {
          if (props.topic?.id !== tid) return
          previewFile.value = null
          previewReadError.value = e instanceof Error ? e.message : '读不到这个文件'
        }
      } else {
        previewNamed.value = false
        previewFile.value = null
      }
    }
  } catch (e) {
    toolError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    if (props.topic?.id === tid) {
      toolLoading.value = false
      toolRefreshing.value = false
    }
  }
}

// Panels that go stale while you watch them: 芝士 commits mid-look and the Git
// panel still shows the moment it was opened; a turn finishes and 资源 still
// shows the count from before. Both re-fetch on a timer, and immediately when a
// turn ends (the moment their numbers actually change).
const REFRESHABLE = new Set(['git', 'resources'])
const TOOL_REFRESH_MS = 20_000
let refreshTimer: ReturnType<typeof setInterval> | null = null

function stopAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = null
}

function refreshTool() {
  const key = openTool.value
  if (!key || !drawerOpen.value || !REFRESHABLE.has(key)) return
  loadTool(key, { silent: true })
}

watch(
  [drawerOpen, openTool],
  ([open, key]) => {
    stopAutoRefresh()
    if (!open || !key || !REFRESHABLE.has(key)) return
    refreshTimer = setInterval(() => {
      // A hidden tab polling forever is pure waste — it re-fetches on the next
      // tick after it comes back anyway.
      if (typeof document !== 'undefined' && document.hidden) return
      refreshTool()
    }, TOOL_REFRESH_MS)
  },
  { immediate: true }
)

watch(
  () => props.working,
  (now, before) => {
    if (before && !now) refreshTool()
  }
)

onBeforeUnmount(stopAutoRefresh)

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif'])
function isImagePath(path: string): boolean {
  return IMAGE_EXT.has(path.split('.').pop()?.toLowerCase() ?? '')
}
const openIsImage = computed(() => !!openPath.value && isImagePath(openPath.value))
// Raw bytes of the open file: what <img> renders for an image, and what the
// download button hands over for anything else that can't be shown as text.
const openRawUrl = computed(() =>
  openPath.value && projectId.value ? workspaceFileRawUrl(projectId.value, openPath.value, props.topic?.id) : ''
)

// 文件 panel state is per-topic. openPath/fileDraft describe a file in the
// CURRENT topic's worktree, so a topic switch must drop them: carrying them over
// meant the next 保存 wrote topic A's draft into topic B's tree, at A's path.
function resetFilePanel() {
  files.value = []
  openPath.value = null
  fileDraft.value = ''
  fileSaved.value = ''
  fileVersion.value = null
  fileBinary.value = false
  fileTooLarge.value = false
  fileBytes.value = 0
  fileConflict.value = false
  expandedDirs.value = new Set()
}

async function selectFile(path: string) {
  const pid = projectId.value
  const tid = props.topic?.id
  if (!pid) return
  toolError.value = null
  fileConflict.value = false
  const listed = files.value.find((f) => f.path === path)?.bytes ?? 0
  // Images render as images — Monaco would show mangled bytes.
  if (isImagePath(path)) {
    openPath.value = path
    fileDraft.value = ''
    fileSaved.value = ''
    fileVersion.value = null
    fileBinary.value = false
    fileTooLarge.value = false
    fileBytes.value = listed
    revealInTree(path)
    return
  }
  try {
    const f = await readFile(pid, path, tid)
    // A topic switch mid-flight must not land the previous topic's file — and
    // its draft — in the new topic's panel.
    if (props.topic?.id !== tid) return
    openPath.value = path
    // Binary and oversized files arrive with no content: they open read-only,
    // so the draft stays empty and there is nothing to write back.
    fileDraft.value = f.content ?? ''
    fileSaved.value = f.content ?? ''
    fileVersion.value = f.version
    fileBinary.value = f.binary
    fileTooLarge.value = f.too_large
    fileBytes.value = f.bytes ?? listed
    revealInTree(path)
  } catch (e) {
    if (props.topic?.id !== tid) return
    toolError.value = e instanceof Error ? e.message : '读取文件失败'
  }
}

// One write path. `expected` is the version this save is based on; null means
// the human explicitly chose to overwrite after being shown the conflict.
async function writeOpenFile(expected: string | null) {
  const pid = projectId.value
  const tid = props.topic?.id
  const path = openPath.value
  if (!pid || !path || fileReadOnly.value || !fileDirty.value || fileSaving.value) return
  const draft = fileDraft.value
  fileSaving.value = true
  toolError.value = null
  try {
    const res = await writeFile(pid, path, draft, tid, expected)
    // The answer is only about the file that was open in the topic that was
    // open — anything else finished after a switch and must be dropped.
    if (props.topic?.id !== tid || openPath.value !== path) return
    fileSaved.value = draft
    fileVersion.value = res.version
    fileConflict.value = false
  } catch (e) {
    if (props.topic?.id !== tid || openPath.value !== path) return
    if (e instanceof ApiError && e.status === 409) {
      // 芝士 wrote this file since it was read. Neither side wins by default:
      // show the conflict and let the human reload or overwrite on purpose.
      fileConflict.value = true
    } else {
      toolError.value = e instanceof Error ? e.message : '保存失败'
    }
  } finally {
    if (props.topic?.id === tid) fileSaving.value = false
  }
}

function saveFile() {
  void writeOpenFile(fileVersion.value)
}

// 冲突后的两条出路,都由人点：丢掉自己的改动看最新的，或者明知有冲突仍然覆盖。
function overwriteFile() {
  void writeOpenFile(null)
}

function reloadOpenFile() {
  const path = openPath.value
  if (path) void selectFile(path)
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

// ---- 军规 1: never silently drop content. ----
// The raw doc exactly as stored on the server (the git-tracked markdown file).
const rawDoc = ref<string>('')
// The `# title\n` line stripped for display (the panel shows the topic title
// itself); re-prepended on save so the FILE keeps its heading.
const titlePrefix = ref<string>('')
// Lossy load detected: parse→serialize differs from the file beyond the
// tolerances documented in docMarkdown.ts. Autosave pauses; manual save asks.
const lossy = ref(false)
const lossyConfirmOpen = ref(false)
// 源码模式: edit the raw markdown in Monaco — the lossless escape hatch.
const sourceMode = ref(false)
const sourceDraft = ref('')
// 军规 1: unsaved edits that a mode switch could NOT carry over (see
// planSourceModeEntry). Held here and offered back in the UI instead of being
// dropped — the 「用源码模式」 button used to delete them outright. A stack, so
// a second set-aside can't overwrite the first.
const pendingEdits = ref<string[]>([])
const hasPendingEdits = computed(() => pendingEdits.value.length > 0)
// 军规 1: a newer version arrived from the server while we had unsaved local
// edits. Neither side wins silently; both are held until the user picks.
const externalDoc = ref<string | null>(null)

// 军规 1: the header used to show 「编辑中…」 while a lossy doc's autosave was
// paused — the edits were stranded in memory and would NEVER be written. The
// status now names that state instead of impersonating a save in progress.
const saveStatus = computed(() =>
  docSaveStatus({
    loading: loading.value,
    saving: saving.value,
    dirty: dirty.value,
    lossy: lossy.value,
    sourceMode: sourceMode.value,
    editable: editable.value,
    savedAt: savedAt.value,
  })
)
const paused = computed(() =>
  autosavePaused({
    dirty: dirty.value,
    lossy: lossy.value,
    sourceMode: sourceMode.value,
    editable: editable.value,
  })
)
const pausedHint = computed(() =>
  editable.value
    ? '此文档含编辑器不完全支持的语法，自动保存已暂停；切到源码模式编辑即可保存'
    : '只读模式下不会自动保存；切回编辑模式即可保存这些改动'
)

// Full markdown the file should contain if we saved right now.
function currentFullMarkdown(): string {
  if (sourceMode.value) return sourceDraft.value
  const ed = editor.value
  if (!ed) return rawDoc.value
  return titlePrefix.value + serializeDoc(ed)
}

// Compare the loaded markdown against its immediate parse→serialize round
// trip. Runs on every load/reload; result drives the banner + autosave pause.
function checkFidelity(md: string) {
  const ed = editor.value
  if (!ed) return
  const report = compareRoundTrip(md, serializeDoc(ed))
  lossy.value = !report.clean
  if (!report.clean) {
    console.debug('[doc] lossy load detected — visual edit would rewrite these lines:\n' + report.diff)
  }
}

// 结构化 token 装饰 (spec §9.1): decorate our OWN tokens — <@handle> /
// <#topicId> — as clickable chips in the doc, read-only and edit alike.
// Deterministic token parsing, never NL guessing.
const TOKEN_RE = /<@([\w-]+)>|<#([0-9a-fA-F-]{8,})>|<&([\w./\u4e00-\u9fff-]+)>/g

// Build the pretty chip element a token renders as. The raw token stays in the
// document (markdown is the source of truth); the chip is display-only.
function tokenWidget(kind: '@' | '#' | '&', id: string, lookupTopic: (tid: string) => string | undefined): HTMLElement {
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

function tokenDecorations(doc: PMNode, lookupTopic: (tid: string) => string | undefined): DecorationSet {
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
        Decoration.inline(from, to, { style: 'display: none' })
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
          apply: (tr, old) => (tr.docChanged ? tokenDecorations(tr.doc, lookupTopicTitle) : old),
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
  // Comment underline → scroll to its card at the doc bottom.
  const ca = target?.closest('.comment-anchor') as HTMLElement | null
  if (ca?.dataset.comment) {
    scrollToCommentCard(ca.dataset.comment)
    return
  }
  // Links: in READ mode the rendered <a target=_blank> navigates natively; in
  // EDIT mode a plain click places the caret and ⌘/Ctrl-click opens the link
  // (the editor-standard gesture, same as VS Code / Feishu).
  const a = target?.closest('.doc-editor a[href]') as HTMLAnchorElement | null
  if (a && editable.value) {
    if (e.metaKey || e.ctrlKey) {
      e.preventDefault()
      window.open(a.href, '_blank', 'noopener')
    }
    return
  }
  const el = target?.closest('.mention') as HTMLElement | null
  if (!el) return
  if (el.dataset.topic) emit('open-topic', el.dataset.topic)
  else if (el.dataset.handle) emit('mention-click', el.dataset.handle)
  else if (el.dataset.file) void openFileRef(el.dataset.file)
}

// ---- Code block copy (hover, like the chat's quiet .im-act buttons). The
// button is an overlay OUTSIDE the editable DOM (ProseMirror reverts foreign
// children), positioned over the hovered <pre>'s top-right corner. ----
const codeCopy = ref<{ top: number; right: number; done: boolean } | null>(null)
let codeCopyPre: HTMLElement | null = null

function onDocMouseOver(e: MouseEvent) {
  const t = e.target as HTMLElement | null
  // Hovering the overlay controls themselves must not unmount them.
  if (t?.closest('.doc-codecopy') || t?.closest('.doc-codelang')) return
  const pre = t?.closest('.doc-editor pre') as HTMLElement | null
  if (!pre) {
    if (!codeLangOpen.value) {
      codeCopy.value = null
      codeCopyPre = null
    }
    return
  }
  if (pre === codeCopyPre && codeCopy.value) return
  codeLangOpen.value = false
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  if (!wrap) return
  const wr = wrap.getBoundingClientRect()
  const pr = pre.getBoundingClientRect()
  codeCopyPre = pre
  codeCopy.value = { top: pr.top - wr.top + 4, right: pr.right - wr.left - 10, done: false }
}

// Curated language choices for the picker (all present in lowlight common).
const CODE_LANGS = [
  'python',
  'typescript',
  'javascript',
  'bash',
  'json',
  'yaml',
  'sql',
  'html',
  'css',
  'go',
  'rust',
  'java',
  'c',
  'cpp',
  'markdown',
  'plaintext',
]
const codeLangOpen = ref(false)

// The open language menu must dismiss on ANY outside interaction, including
// clicks far outside the editor — document-level capture listener while open.
function onGlobalPointerDown(e: MouseEvent) {
  if (!(e.target as HTMLElement | null)?.closest('.doc-codelang')) {
    codeLangOpen.value = false
  }
}
function onGlobalKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') codeLangOpen.value = false
}
watch(codeLangOpen, (open) => {
  if (open) {
    document.addEventListener('mousedown', onGlobalPointerDown, true)
    document.addEventListener('keydown', onGlobalKeydown, true)
  } else {
    document.removeEventListener('mousedown', onGlobalPointerDown, true)
    document.removeEventListener('keydown', onGlobalKeydown, true)
  }
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onGlobalPointerDown, true)
  document.removeEventListener('keydown', onGlobalKeydown, true)
})

function currentCodeLang(): string {
  return codeCopyPre?.getAttribute('data-language') || '语言'
}

function setCodeBlockLang(lang: string) {
  codeLangOpen.value = false
  const ed = editor.value
  if (!ed || !codeCopyPre) return
  try {
    const pos = ed.view.posAtDOM(codeCopyPre, 0)
    const $pos = ed.state.doc.resolve(pos)
    // Walk up to the codeBlock node and rewrite its language attr.
    for (let d = $pos.depth; d >= 0; d--) {
      const node = $pos.node(d)
      if (node.type.name === 'codeBlock') {
        const at = d > 0 ? $pos.before(d) : 0
        const tr = ed.state.tr.setNodeMarkup(at, undefined, {
          ...node.attrs,
          language: lang === 'plaintext' ? null : lang,
        })
        ed.view.dispatch(tr)
        return
      }
    }
  } catch {
    // best-effort — the block may have moved; the next hover re-anchors
  }
}

async function copyCodeBlock() {
  if (!codeCopyPre || !codeCopy.value) return
  try {
    await navigator.clipboard.writeText(codeCopyPre.innerText.replace(/\n$/, ''))
    codeCopy.value = { ...codeCopy.value, done: true }
    window.setTimeout(() => {
      if (codeCopy.value) codeCopy.value = { ...codeCopy.value, done: false }
    }, 1200)
  } catch {
    errorMsg.value = '复制失败'
  }
}

// An underlined quote scrolls to its comment card at the doc bottom and
// pulses it (the reverse jump — card→paragraph — already exists via the chip).
function scrollToCommentCard(commentId: string) {
  commentsFolded.value = false
  void nextTick(() => {
    const card = document.querySelector(`[data-comment-card="${commentId}"]`)
    card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    card?.classList.add('comment-card--pulse')
    window.setTimeout(() => card?.classList.remove('comment-card--pulse'), 1600)
  })
}

// A <&path> chip opens that file in the 文件 drawer's editor.
async function openFileRef(path: string) {
  openTool.value = 'files'
  drawerOpen.value = true
  await loadTool('files')
  await selectFile(path)
}

// Display-time image src resolution: workspace-relative paths (uploads/x.png)
// render through the raw-file API; absolute http(s)/data URLs pass through.
// The node attr keeps the ORIGINAL path — serialization writes it back
// verbatim, so a localhost URL never leaks into the markdown file.
function resolveImageSrc(src: string): string {
  if (/^(https?:|data:|blob:|\/)/i.test(src)) return src
  const pid = projectId.value
  if (!pid) return src
  return workspaceFileRawUrl(pid, src.replace(/^\.\//, ''), props.topic?.id)
}

// Dev-only probe hook: lets Playwright inspect serialization/dirty state
// without guessing at DOM classes (observability rule).
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__docPanel = {
    getMarkdown: () => (editor.value ? serializeDoc(editor.value) : null),
    isDirty: () => dirty.value,
    // 军规 1 state: probes assert that nothing was dropped, not that a class
    // name happened to render.
    saveStatus: () => saveStatus.value,
    isAutosavePaused: () => paused.value,
    pendingEdits: () => [...pendingEdits.value],
    externalDoc: () => externalDoc.value,
    updates: 0,
  }
}

// ---- Notion-style slash menu. Typing "/" at the start of a block (an empty
// paragraph or the head of a non-empty one) opens a floating block-type menu;
// picking an item converts the block IN PLACE (turn-into), keeping its text.
// Built on @tiptap/suggestion: the "/" is OUR structured trigger token — the
// plugin matches it positionally, never parses natural language (军规 4).
// Lives here (not docMarkdown.ts) because it is pure editor UI — Vue menu
// state, v-icon items, wrap-relative positioning — and it adds NO nodes or
// marks, so the shared round-trip schema is untouched.
interface SlashItem {
  key: string
  label: string
  icon: string
  hint: string
  /** Filter keywords: english names + pinyin (full + initials). */
  keywords: string[]
  /** Applied AFTER the "/query" token is deleted; must keep block text. */
  run: (chain: ChainedCommands) => ChainedCommands
}

// clearNodes() first: it lifts list items / quotes and normalizes the current
// block back to a paragraph, so every conversion starts from the same shape —
// that's what makes 标题↔正文↔列表↔引用 all interconvertible. Text survives;
// 代码块 takes the whole block's text as its code content.
const SLASH_ITEMS: SlashItem[] = [
  {
    key: 'text',
    label: '正文',
    icon: 'mdi-format-paragraph',
    hint: 'text',
    keywords: ['text', 'paragraph', 'p', 'zw', 'zhengwen'],
    run: (c) => c.clearNodes(),
  },
  {
    key: 'h1',
    label: '标题 1',
    icon: 'mdi-format-header-1',
    hint: 'h1',
    keywords: ['h1', 'heading1', 'title', 'bt1', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 1 }),
  },
  {
    key: 'h2',
    label: '标题 2',
    icon: 'mdi-format-header-2',
    hint: 'h2',
    keywords: ['h2', 'heading2', 'bt2', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 2 }),
  },
  {
    key: 'h3',
    label: '标题 3',
    icon: 'mdi-format-header-3',
    hint: 'h3',
    keywords: ['h3', 'heading3', 'bt3', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 3 }),
  },
  {
    key: 'bullet',
    label: '无序列表',
    icon: 'mdi-format-list-bulleted',
    hint: 'list',
    keywords: ['ul', 'list', 'bullet', 'wxlb', 'liebiao'],
    run: (c) => c.clearNodes().toggleBulletList(),
  },
  {
    key: 'ordered',
    label: '有序列表',
    icon: 'mdi-format-list-numbered',
    hint: '1.',
    keywords: ['ol', 'list', 'ordered', 'number', 'yxlb', 'liebiao'],
    run: (c) => c.clearNodes().toggleOrderedList(),
  },
  {
    key: 'task',
    label: '任务列表',
    icon: 'mdi-format-list-checks',
    hint: 'todo',
    keywords: ['todo', 'task', 'checkbox', 'rwlb', 'renwu'],
    run: (c) => c.clearNodes().toggleTaskList(),
  },
  {
    key: 'code',
    label: '代码块',
    icon: 'mdi-code-tags',
    hint: 'code',
    keywords: ['code', 'codeblock', 'pre', 'dmk', 'daima'],
    run: (c) => c.clearNodes().setNode('codeBlock'),
  },
  {
    key: 'quote',
    label: '引用',
    icon: 'mdi-format-quote-close',
    hint: 'quote',
    keywords: ['quote', 'blockquote', 'yy', 'yinyong'],
    run: (c) => c.clearNodes().toggleBlockquote(),
  },
  {
    key: 'table',
    label: '表格',
    icon: 'mdi-table',
    hint: 'table',
    keywords: ['table', 'bg', 'biaoge'],
    run: (c) => c.insertTable({ rows: 2, cols: 3, withHeaderRow: true }),
  },
  {
    key: 'hr',
    label: '分割线',
    icon: 'mdi-minus',
    hint: '---',
    keywords: ['hr', 'divider', 'line', 'fgx', 'fengexian'],
    run: (c) => c.setHorizontalRule(),
  },
]

function filterSlashItems(query: string): SlashItem[] {
  const q = query.toLowerCase().trim()
  if (!q) return SLASH_ITEMS
  return SLASH_ITEMS.filter((it) => it.label.includes(q) || it.keywords.some((k) => k.includes(q)))
}

const slashPluginKey = new PluginKey('cheeseSlashMenu')
interface SlashMenuState {
  items: SlashItem[]
  index: number
  top: number
  left: number
}
const slashMenu = ref<SlashMenuState | null>(null)
const slashMenuEl = ref<HTMLElement | null>(null)
// Latest suggestion props — command() routes through the plugin so the
// "/query" range is deleted consistently for keyboard and mouse picks.
let slashProps: SuggestionProps<SlashItem, SlashItem> | null = null
// Set by the ＋ handle: it inserted the "/" itself. If the menu closes while
// that "/" is still alone in its paragraph, we remove it again (Notion does
// exactly this — the slash was UI scaffolding, not user content).
let plusSlashPending = false

// Anchor the floating menu to the caret rect (suggestion's clientRect),
// wrap-relative like every other doc overlay. Flips above the caret when the
// menu would run past the viewport bottom.
function slashMenuPos(
  clientRect: (() => DOMRect | null) | null | undefined,
  itemCount: number
): { top: number; left: number } | null {
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  const rect = clientRect?.()
  if (!wrap || !rect) return null
  const wr = wrap.getBoundingClientRect()
  const est = Math.min(itemCount, 8) * 33 + 10 // menu height estimate (capped)
  const fitsBelow = rect.bottom + 6 + est <= window.innerHeight
  return {
    top: fitsBelow ? rect.bottom - wr.top + 6 : rect.top - wr.top - est - 6,
    left: rect.left - wr.left,
  }
}

function showSlashMenu(p: SuggestionProps<SlashItem, SlashItem>) {
  slashProps = p
  const pos = slashMenuPos(p.clientRect, p.items.length)
  if (!pos || p.items.length === 0) {
    slashMenu.value = null // no matches → menu hides, "/query" stays as text
    return
  }
  // Selection resets to the top on every keystroke (Notion behaviour).
  slashMenu.value = { items: p.items, index: 0, top: pos.top, left: pos.left }
}

function scrollActiveSlashItem() {
  void nextTick(() => {
    slashMenuEl.value?.querySelector('.doc-slash__item--active')?.scrollIntoView({ block: 'nearest' })
  })
}

function runSlashItem(item: SlashItem) {
  slashProps?.command(item)
}

function onSlashKeyDown({ event }: { event: KeyboardEvent }): boolean {
  const m = slashMenu.value
  if (!m) return false // Escape is handled by the plugin itself (exits)
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    const delta = event.key === 'ArrowDown' ? 1 : -1
    m.index = (m.index + delta + m.items.length) % m.items.length
    scrollActiveSlashItem()
    return true
  }
  if (event.key === 'Enter') {
    runSlashItem(m.items[m.index])
    return true
  }
  return false
}

function onSlashExit(p: SuggestionProps<SlashItem, SlashItem>) {
  slashMenu.value = null
  slashProps = null
  if (!plusSlashPending) return
  plusSlashPending = false
  // The ＋ button planted this "/" — if the menu closed without a pick and
  // nothing else was typed, the paragraph still reads exactly "/": clean it.
  try {
    const ed = p.editor
    const $from = ed.state.doc.resolve(p.range.from)
    if ($from.parent.type.name === 'paragraph' && $from.parent.textContent === '/') {
      ed.commands.deleteRange({ from: p.range.from, to: p.range.from + 1 })
    }
  } catch {
    // stale range (block moved/removed) — nothing to clean
  }
}

const SlashCommands = Extension.create({
  name: 'cheeseSlashCommands',
  addProseMirrorPlugins() {
    return [
      Suggestion<SlashItem, SlashItem>({
        editor: this.editor,
        pluginKey: slashPluginKey,
        char: '/',
        // 空段落或行首: the trigger only arms at the head of a text block —
        // mid-sentence "/" (dates, paths) never opens the menu.
        startOfLine: true,
        allowedPrefixes: null,
        items: ({ query }) => filterSlashItems(query),
        allow: ({ state, range }) => {
          // Never inside code blocks ("/" is code) or table cells (block-type
          // conversions there would produce markdown a GFM table can't hold —
          // the round-trip guard would flag the doc as lossy).
          const $from = state.doc.resolve(range.from)
          for (let d = $from.depth; d > 0; d--) {
            const name = $from.node(d).type.name
            if (name === 'codeBlock' || name === 'tableCell' || name === 'tableHeader') {
              return false
            }
          }
          return true
        },
        command: ({ editor: ed, range, props: item }) => {
          // One chain = one undo step: drop the "/query" token, then convert.
          item.run(ed.chain().focus().deleteRange(range)).run()
        },
        render: () => ({
          onStart: showSlashMenu,
          onUpdate: showSlashMenu,
          onExit: onSlashExit,
          onKeyDown: onSlashKeyDown,
        }),
      }),
    ]
  },
})

const editor = useEditor({
  content: '',
  extensions: [...docExtensions({ resolveImageSrc }), TokenChips, LiveRefBadges, CommentMarks, SlashCommands],
  editable: editable.value,
  editorProps: {
    attributes: { class: 'doc-prose' },
  },
  onUpdate: () => {
    if (import.meta.env.DEV) {
      const hook = (window as unknown as Record<string, { updates?: number }>).__docPanel
      if (hook) hook.updates = (hook.updates ?? 0) + 1
    }
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
    cta.nodeIndex >= nodes.length || nodes.length !== contentBlocks().length ? null : nodes[cta.nodeIndex].id
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
  // Notion behaviour: ＋ = "在此块下方插入新块并问它是什么" — a VISIBLE "/"
  // typed into the new block arms the same slash suggestion typing would
  // (the slash is real content; typing filters; onSlashExit removes an
  // orphaned one). Nuance Notion gets right: if the hovered block is ALREADY
  // an empty paragraph, ask in place — spawning another blank line below an
  // empty line reads as a bug.
  plusSlashPending = true
  const hovered = ed.state.doc.nodeAt(hoverPos.value)
  const emptyPara = hovered?.type.name === 'paragraph' && hovered.content.size === 0
  if (emptyPara) {
    ed.chain()
      .focus()
      .setTextSelection(hoverPos.value + 1)
      .insertContent('/')
      .run()
    return
  }
  const insertAt = hoverPos.value + hoverNodeSize.value
  ed.chain()
    .focus()
    .insertContentAt(insertAt, { type: 'paragraph' })
    .setTextSelection(insertAt + 1)
    .insertContent('/')
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
// twice — strip it for DISPLAY but remember the exact prefix: save() prepends
// it again, so the file never loses its heading (pure string equality, no
// guessing, no loss).
function splitDuplicateTitle(md: string): { prefix: string; body: string } {
  const title = props.topic?.title?.trim()
  if (!title) return { prefix: '', body: md }
  const m = md.match(/^#\s+(.+?)\s*\n+/)
  return m && m[1].trim() === title ? { prefix: m[0], body: md.slice(m[0].length) } : { prefix: '', body: md }
}

// Install fresh server content into the panel state (editor + source draft +
// fidelity check). The one place load & reload share.
function installDoc(full: string) {
  const { prefix, body } = splitDuplicateTitle(full)
  rawDoc.value = full
  titlePrefix.value = prefix
  lastSavedMarkdown.value = body
  sourceDraft.value = full
  setEditorMarkdown(body)
  checkFidelity(body)
}

async function loadDoc(topicId: string) {
  errorMsg.value = null
  loading.value = true
  try {
    const block = await getDoc(topicId)
    // Avoid races on fast topic switching.
    if (props.topic?.id !== topicId) return
    installDoc(block?.content ?? '')
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

// Reload triggered by AI activity. Don't clobber unsaved local edits — but
// 军规 1: don't silently drop the server's version either. When both sides
// moved, hold the incoming content and let the conflict bar decide.
async function reloadFromActivity(topicId: string) {
  // A save in flight makes any snapshot ambiguous: the server may or may not
  // have our PUT yet, so a difference here proves nothing. Skip; the next
  // activity tick compares against a settled rawDoc.
  if (saving.value) return
  try {
    const block = await getDoc(topicId)
    if (props.topic?.id !== topicId || saving.value) return
    const full = block?.content ?? ''
    const plan = planExternalUpdate({ dirty: dirty.value, incoming: full, rawDoc: rawDoc.value })
    if (plan === 'install') {
      installDoc(full)
      externalDoc.value = null
      savedAt.value = null
    } else if (plan === 'conflict') {
      externalDoc.value = full
    } else {
      externalDoc.value = null
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
// 军规 1: when a lossy load was detected, VISUAL-mode autosave is paused —
// writing the round-tripped doc back would destroy the unsupported syntax.
// Source mode edits the raw text, so its autosave is always safe.
let autosaveTimer: ReturnType<typeof setTimeout> | null = null
function queueAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(() => {
    if (lossy.value && !sourceMode.value) return
    if (dirty.value && editable.value && !saving.value) void save()
  }, 2500)
}

function onDocKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    if (dirty.value && editable.value) void save()
  }
}

async function save(force = false) {
  const topic = props.topic
  if (!topic || saving.value) return
  // Lossy visual save needs explicit confirmation (源码模式 is the safe path).
  if (lossy.value && !sourceMode.value && !force) {
    lossyConfirmOpen.value = true
    return
  }
  const full = currentFullMarkdown()
  if (full === rawDoc.value) {
    dirty.value = false
    return
  }
  saving.value = true
  errorMsg.value = null
  try {
    await putDoc(topic.id, full, AUTHOR)
    rawDoc.value = full
    lastSavedMarkdown.value = splitDuplicateTitle(full).body
    if (!sourceMode.value) sourceDraft.value = full
    savedAt.value = Date.now()
    // Lost-update guard: an edit that landed WHILE this save was in flight
    // (e.g. a code-block language pick right after typing) must not have its
    // dirty flag wiped by our completion — compare against what we actually
    // shipped, and re-queue if the doc moved on.
    if (currentFullMarkdown() === full) {
      dirty.value = false
    } else {
      dirty.value = true
      queueAutosave()
    }
    // A confirmed lossy overwrite: what's on disk now IS the editor's view.
    if (force) lossy.value = false
    // Our version is the file now — the conflict (if any) is resolved.
    externalDoc.value = null
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

// The lossy-confirm dialog's 「仍要保存」.
function confirmLossySave() {
  lossyConfirmOpen.value = false
  void save(true)
}

function onBlur() {
  if (dirty.value && !(lossy.value && !sourceMode.value)) save()
}

function toggleEditable() {
  editable.value = !editable.value
  // emitUpdate=false: tiptap v3's setEditable fires a synthetic 'update' by
  // default (no doc change!) — our onUpdate would mark the doc dirty and,
  // in readonly, autosave never runs, so 「编辑中…」 stuck forever.
  editor.value?.setEditable(editable.value, false)
  // Leaving edit mode with unsaved content = save now (飞书 semantics).
  if (!editable.value && dirty.value) void save()
}

// ---- 源码模式: raw markdown in Monaco. Entering shows the exact file
// content (or the current unsaved visual edits, serialized); leaving parses
// the draft back into the visual editor and re-runs the fidelity check. ----
// 军规 1: on a lossy doc the source view must show the FILE, never the degraded
// serialization — otherwise the escape hatch itself corrupts the syntax it
// exists to protect. But the user's unsaved visual edits live ONLY in that
// serialization, so they are stashed (pendingEdits) and offered back by the
// bar above the editor. Nothing is dropped; the user decides.
function enterSourceMode() {
  lossyConfirmOpen.value = false
  const plan = planSourceModeEntry({
    lossy: lossy.value,
    dirty: dirty.value,
    rawDoc: rawDoc.value,
    visualMarkdown: currentFullMarkdown(),
  })
  if (plan.stashed !== null) pendingEdits.value = pushStash(pendingEdits.value, plan.stashed)
  sourceDraft.value = plan.draft
  dirty.value = plan.dirty
  sourceMode.value = true
  // Source-mode autosave is never paused, so edits carried in here must not be
  // left stranded waiting for the next keystroke.
  if (dirty.value) queueAutosave()
}

function exitSourceMode() {
  sourceMode.value = false
  const { prefix, body } = splitDuplicateTitle(sourceDraft.value)
  titlePrefix.value = prefix
  setEditorMarkdown(body)
  checkFidelity(body)
  dirty.value = sourceDraft.value !== rawDoc.value
  if (dirty.value) queueAutosave()
}

// 「恢复我的改动」: put the stashed edits back into whichever editor is showing.
// A swap — what was on screen goes back onto the stash, so restoring can't be
// the step that drops content either.
function applyPendingEdits() {
  const { restored, stack } = popStash(pendingEdits.value, currentFullMarkdown(), rawDoc.value)
  if (restored === null) return
  pendingEdits.value = stack
  if (sourceMode.value) {
    sourceDraft.value = restored
  } else {
    const { prefix, body } = splitDuplicateTitle(restored)
    titlePrefix.value = prefix
    setEditorMarkdown(body)
  }
  dirty.value = restored !== rawDoc.value
  if (dirty.value) {
    savedAt.value = null
    queueAutosave()
  }
}

function discardPendingEdits() {
  pendingEdits.value = dropStash(pendingEdits.value)
}

// 冲突条「查看磁盘版本」: open the server's version in source mode and stash the
// local edits so they stay recoverable — the same never-drop mechanism.
function viewExternalDoc() {
  const incoming = externalDoc.value
  if (incoming === null) return
  pendingEdits.value = pushStash(pendingEdits.value, currentFullMarkdown())
  externalDoc.value = null
  // The server version becomes the new base; the local edits sit in the stash,
  // one click away, instead of being clobbered by the incoming content.
  installDoc(incoming)
  dirty.value = false
  savedAt.value = null
  sourceMode.value = true
}

// 冲突条「用我的版本覆盖」: an explicit overwrite, never an implicit one.
function overwriteWithMine() {
  externalDoc.value = null
  void save(true)
}

function toggleSourceMode() {
  if (sourceMode.value) exitSourceMode()
  else enterSourceMode()
}

// Typing in the source editor: same dirty + autosave contract as the visual
// editor (source autosave is never paused — raw text can't be lossy).
function onSourceInput(v: string) {
  sourceDraft.value = v
  if (loadingFromServer.value) return
  dirty.value = v !== rawDoc.value
  if (dirty.value) {
    savedAt.value = null
    queueAutosave()
  }
}

// Topic switch: full reload.
watch(
  () => props.topic?.id,
  (id) => {
    // Doc fidelity state is per-topic — reset before the new doc loads.
    sourceMode.value = false
    lossy.value = false
    lossyConfirmOpen.value = false
    pendingEdits.value = []
    externalDoc.value = null
    codeCopy.value = null
    if (id) loadDoc(id)
    else {
      lastSavedMarkdown.value = ''
      rawDoc.value = ''
      titlePrefix.value = ''
      sourceDraft.value = ''
      dirty.value = false
      setEditorMarkdown('')
    }
    // Close the drawer on topic switch so it doesn't carry over.
    openTool.value = null
    drawerOpen.value = false
    // Drop the previous topic's terminal so it can't flash in the new 现场.
    terminalUrl.value = null
    // Same for the preview: a stale app frame or error would otherwise be
    // attributed to the topic just opened.
    previewAppUrl.value = null
    previewAppNote.value = ''
    previewNamedPath.value = ''
    previewFile.value = null
    previewError.value = null
    previewReadError.value = null
    previewFull.value = false
    // …and the previous topic's file + draft, which would otherwise be saved
    // into THIS topic's worktree the next time 保存 is pressed.
    resetFilePanel()
  },
  { immediate: true }
)

// AI activity: soft reload (respects unsaved edits).
watch(
  () => props.activityTick,
  () => {
    const id = props.topic?.id
    if (id) reloadFromActivity(id)
  }
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
  { deep: true }
)

onBeforeUnmount(() => {
  editor.value?.destroy()
})
</script>

<template>
  <div class="doc d-flex flex-column fill-height" style="position: relative">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
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

        <span v-if="saveStatus === 'loading'" class="t-meta me-2">加载中…</span>
        <span v-else-if="saveStatus === 'saving'" class="t-meta me-2">保存中…</span>
        <!-- 军规 1: autosave is paused — say so instead of faking progress. -->
        <span v-else-if="saveStatus === 'paused'" class="doc-status-paused me-2" :title="pausedHint">
          <v-icon size="13">mdi-pause-circle-outline</v-icon>
          已暂停 · 改动未保存
        </span>
        <span
          v-else-if="saveStatus === 'saved'"
          class="d-inline-flex align-center ga-1 c-faint me-2"
          style="font-size: 12px"
        >
          <span class="status-dot status-dot--ok" />已保存
        </span>
        <span v-else-if="saveStatus === 'dirty'" class="t-meta me-2">编辑中…</span>

        <v-btn size="small" variant="text" class="me-1 c-muted" :disabled="sourceMode" @click="toggleEditable">
          {{ editable ? '只读' : '编辑' }}
        </v-btn>
        <!-- 源码: raw markdown in Monaco — the lossless escape hatch for any
             syntax the visual editor can't fully represent (军规 1). -->
        <v-btn
          size="small"
          variant="text"
          class="me-1"
          :class="sourceMode ? 'tool-btn--active' : 'c-muted'"
          title="源码模式（直接编辑 markdown 原文）"
          @click="toggleSourceMode"
        >
          源码
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

      <!-- 军规 1 notices. Above the stage so they show in BOTH visual and
           source mode — the states they describe survive a mode switch. -->
      <!-- Edits a mode switch could not carry over: held, not dropped. -->
      <div v-if="hasPendingEdits" class="doc-notice">
        <v-icon size="16" class="doc-notice__icon">mdi-content-save-alert-outline</v-icon>
        <div class="doc-notice__text">
          有未保存的改动没有带入当前编辑器（编辑器显示的是磁盘上的版本）。改动仍然保留着，可以随时取回。
          <template v-if="pendingEdits.length > 1">共 {{ pendingEdits.length }} 份，先取回最近一份。</template>
        </div>
        <button type="button" class="doc-notice__btn" @click="applyPendingEdits">恢复我的改动</button>
        <button type="button" class="doc-notice__btn doc-notice__btn--quiet" @click="discardPendingEdits">丢弃</button>
      </div>
      <!-- Server and local both moved: neither side wins silently. -->
      <div v-if="externalDoc !== null" class="doc-notice doc-notice--conflict">
        <v-icon size="16" class="doc-notice__icon">mdi-source-branch</v-icon>
        <div class="doc-notice__text">芝士更新了磁盘上的这篇文档，而你有未保存的改动。两份都还在，选一份继续。</div>
        <button type="button" class="doc-notice__btn" @click="viewExternalDoc">查看磁盘版本</button>
        <button type="button" class="doc-notice__btn" @click="overwriteWithMine">用我的版本覆盖</button>
      </div>

      <!-- Stage: the editor + (optionally) a docked tool panel beside it. -->
      <div class="doc-stage flex-grow-1">
        <!-- 源码模式: the raw markdown file in Monaco. Full-bleed (no page
           column) — this is the file itself, not the document view. -->
        <div v-if="sourceMode" class="doc-source" @keydown="onDocKeydown">
          <CodeEditor
            :model-value="sourceDraft"
            filename="doc.md"
            :readonly="!editable"
            @update:model-value="onSourceInput"
            @save="save()"
          />
        </div>
        <!-- Editor surface — a Feishu Docs page: white, padded, centered column. -->
        <div
          v-else
          class="doc-body overflow-y-auto"
          :class="{ readonly: !editable }"
          @focusout="onBlur"
          @scroll.passive="codeCopy = null"
        >
          <div class="doc-page" :class="{ 'doc-pulse': pulsing }">
            <!-- Large document title (Feishu Docs), = the topic title -->
            <h1 class="doc-page__title">{{ topic.title }}</h1>
            <!-- 军规 1 banner: this doc uses syntax the visual editor can't
               fully represent — autosave is paused, source mode is lossless. -->
            <div v-if="lossy" class="doc-lossy-banner">
              <v-icon size="16" class="doc-lossy-banner__icon">mdi-alert-outline</v-icon>
              <div class="doc-lossy-banner__text">
                此文档包含编辑器暂不完全支持的语法，可视化编辑保存可能丢失格式。 自动保存已暂停——建议用源码模式编辑。
              </div>
              <button type="button" class="doc-lossy-banner__btn" @click="enterSourceMode()">源码模式</button>
            </div>
            <div class="doc-editor-wrap" @click="onDocClick" @keydown="onDocKeydown" @mouseover="onDocMouseOver">
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
              <!-- Notion-style slash menu: anchored to the caret (suggestion
                 clientRect), wrap-relative like the other overlays. Keyboard
                 (↑↓/Enter/Esc) is handled in the suggestion plugin; the mouse
                 path routes through the same command(). -->
              <div
                v-if="slashMenu"
                ref="slashMenuEl"
                class="doc-slash__menu"
                :style="{ top: `${slashMenu.top}px`, left: `${slashMenu.left}px` }"
              >
                <button
                  v-for="(it, i) in slashMenu.items"
                  :key="it.key"
                  type="button"
                  class="doc-slash__item"
                  :class="{ 'doc-slash__item--active': i === slashMenu.index }"
                  @mousedown.prevent
                  @mouseenter="slashMenu.index = i"
                  @click="runSlashItem(it)"
                >
                  <v-icon size="15" class="doc-slash__icon">{{ it.icon }}</v-icon>
                  <span class="doc-slash__label">{{ it.label }}</span>
                  <span class="doc-slash__hint">{{ it.hint }}</span>
                </button>
              </div>
              <!-- Code-block hover toolbar: ONE right-anchored flex bar
                 ([language ∨][copy]) growing leftward — the two controls can
                 no longer overlap however long the language name gets. -->
              <div
                v-if="codeCopy"
                class="doc-codebar"
                :style="{ top: `${codeCopy.top}px`, left: `${codeCopy.right}px` }"
              >
                <div v-if="editable" class="doc-codelang">
                  <button type="button" class="doc-codelang__chip" @click="codeLangOpen = !codeLangOpen">
                    {{ currentCodeLang() }}
                    <v-icon size="12">mdi-chevron-down</v-icon>
                  </button>
                  <div v-if="codeLangOpen" class="doc-codelang__menu">
                    <button
                      v-for="l in CODE_LANGS"
                      :key="l"
                      type="button"
                      class="doc-codelang__item"
                      @click="setCodeBlockLang(l)"
                    >
                      {{ l }}
                    </button>
                  </div>
                </div>
                <button
                  type="button"
                  class="doc-codecopy"
                  :class="{ 'doc-codecopy--done': codeCopy.done }"
                  :title="codeCopy.done ? '已复制' : '复制代码'"
                  @mousedown.prevent
                  @click="copyCodeBlock"
                >
                  <v-icon size="14">
                    {{ codeCopy.done ? 'mdi-check' : 'mdi-content-copy' }}
                  </v-icon>
                </button>
              </div>
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

            <!-- 飞书 docs 风常驻评论区: ALL comments live at the bottom of the
               document (评论归一 — the drawer tool is gone). Anchored comments
               carry a quote chip that scrolls + flashes their paragraph;
               page-level comments render plain. -->
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
                  <span v-if="comments.length" class="doc-comments__count">
                    {{ comments.length }}
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
                <div v-for="c in comments" :key="c.id" class="doc-comments__item" :data-comment-card="c.id">
                  <span class="doc-comments__avatar">
                    {{ (c.author || '?').slice(0, 1).toUpperCase() }}
                  </span>
                  <div class="doc-comments__main">
                    <div class="doc-comments__meta">
                      <span class="doc-comments__author">{{ c.author }}</span>
                      <span class="t-meta">{{ relTime(c.created_at) }}</span>
                    </div>
                    <!-- Anchored comment: quoted-span chip → scroll & flash its
                       paragraph. A dead anchor — the node id no longer resolves,
                       or the node row was deleted and the FK nulled reply_to
                       (leaving only the quote) — says so instead of a dead chip. -->
                    <button
                      v-if="c.reply_to && commentAnchor(c)"
                      type="button"
                      class="doc-comments__chip"
                      title="定位到该段"
                      @click="highlightNode(c.reply_to!)"
                    >
                      {{ c.anchor_quote || nodeLabel(commentAnchor(c)!.content) }}
                    </button>
                    <div v-else-if="c.reply_to || c.anchor_quote" class="doc-comments__stale">原段落已改动</div>
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
            <div v-if="pinned" class="tool-resizer" title="拖动调整宽度" @mousedown="startToolResize" />
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
                v-if="openTool && REFRESHABLE.has(openTool)"
                icon="mdi-refresh"
                size="small"
                variant="text"
                class="c-muted"
                title="刷新"
                :loading="toolRefreshing"
                @click="refreshTool"
              />
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

            <div ref="toolContentRef" class="tool-content">
              <div v-if="toolLoading" class="d-flex justify-center py-8">
                <v-progress-circular indeterminate color="primary" size="28" />
              </div>
              <v-alert v-else-if="toolError" type="error" density="compact" class="ma-4">
                {{ toolError }}
              </v-alert>

              <!-- 现场: real terminal (tmux backend) OR read-only transcript timeline -->
              <template v-else-if="openTool === 'site' && terminalUrl">
                <!-- 实时终端(只读): the topic container's ttyd pane, proxied by the
                 backend. iframe is the simplest embed — ttyd ships its own
                 xterm.js frontend, and same-origin (via the /api proxy) means no
                 CSP/cross-origin friction. Read-only mirror (ttyd -R). -->
                <div class="term-wrap">
                  <div class="term-bar text-caption px-3 py-1">
                    <span class="term-bar__dot">●</span>
                    实时终端（只读）
                  </div>
                  <iframe class="term-frame" :src="terminalUrl" title="实时终端（只读）" />
                </div>
              </template>

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
                      <span class="site-act__dot" :class="{ 'site-act__dot--platform': eventPlatform(b) }">●</span>
                      <div class="site-act__body">
                        <span class="site-act__verb">{{ eventVerb(b) }}</span>
                        <div v-if="eventArg(b)" class="site-act__arg">⎿ {{ eventArg(b) }}</div>
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
                        <div
                          class="site-msg__raw"
                          :class="{ 'site-msg__raw--clamped': isLongSiteEntry(b.content) && !expandedSite.has(b.id) }"
                          :style="{ '--site-clamp-lines': SITE_CLAMP_LINES }"
                        >
                          {{ b.content }}
                        </div>
                        <!-- 过长时不直接摊开：一条几千字的输出会把它前后的所有
                             东西挤出屏幕，而 现场 的价值恰恰是「一眼看完发生了
                             什么」。折叠到 12 行，想看全的自己点开。 -->
                        <button
                          v-if="isLongSiteEntry(b.content)"
                          type="button"
                          class="site-msg__more"
                          @click="toggleSiteEntry(b.id)"
                        >
                          {{ expandedSite.has(b.id) ? '收起' : `展开全部（${countLines(b.content)} 行）` }}
                        </button>
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
                        >●</span
                      >
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
                      <template v-if="liveElapsed !== null"> （{{ liveElapsed }}s） </template>
                    </span>
                  </div>
                </div>
              </template>

              <!-- Git: this topic's own commits + the diff its 采纳 would merge.
               Both are topic-scoped; the project-level view is other topics'
               work and was what made this panel lie. -->
              <template v-else-if="openTool === 'git'">
                <div class="pa-3">
                  <div class="t-eyebrow mb-2">本话题提交</div>
                  <div v-if="gitCommits.length === 0" class="text-medium-emphasis text-body-2 mb-3">
                    本话题还没有自己的提交（采纳后它们会并入主干）
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
                  <div class="t-eyebrow mb-2">本话题改动（相对主干）</div>
                  <pre v-if="gitDiff.trim()" class="code-pre">{{ gitDiff }}</pre>
                  <div v-else class="text-medium-emphasis text-body-2">本话题还没有改动</div>
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
                    <!-- Read-only files (binary / oversized / images) get no 保存
                     button at all: saving one is what corrupted them. -->
                    <span v-if="fileReadOnly && openPath" class="file-bar__ro">只读</span>
                    <v-btn
                      v-else
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
                  <!-- 保存冲突: 芝士 wrote this file after it was read. Show it and
                   let the human choose — a silent winner is how edits vanished. -->
                  <div v-if="fileConflict" class="file-conflict">
                    <v-icon size="15" class="me-1">mdi-alert-outline</v-icon>
                    <span class="file-conflict__text">
                      这个文件在你编辑期间被改过（多半是芝士写的）。直接保存会盖掉那些改动。
                    </span>
                    <v-btn size="x-small" variant="text" @click="reloadOpenFile">放弃我的修改，看最新的</v-btn>
                    <v-btn size="x-small" variant="text" color="error" :loading="fileSaving" @click="overwriteFile">
                      仍然覆盖保存
                    </v-btn>
                  </div>
                  <div class="file-body">
                    <div v-if="fileListOpen" ref="fileListEl" class="file-list">
                      <div v-if="files.length === 0" class="text-center c-faint py-6" style="font-size: 0.8rem">
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
                        <img :src="openRawUrl" :alt="openPath" />
                      </div>
                      <!-- Binary / oversized: no editor. Opening one in Monaco
                       meant every byte utf-8 could not decode came back as
                       U+FFFD, and 保存 wrote the damage to disk. -->
                      <div v-else-if="openPath && fileReadOnly" class="file-blob">
                        <v-icon size="30" class="c-faint mb-2">
                          {{ fileTooLarge ? 'mdi-weight' : 'mdi-file-code-outline' }}
                        </v-icon>
                        <div class="file-blob__title">
                          {{ fileTooLarge ? '文件太大，不在浏览器里打开' : '二进制文件，不能当文本编辑' }}
                        </div>
                        <div class="file-blob__note">
                          {{ openPath }} · {{ fmtBytes(fileBytes) }}
                          <template v-if="!fileTooLarge"> —— 按文本打开会改坏它，所以这里只读。 </template>
                        </div>
                        <v-btn
                          size="small"
                          variant="tonal"
                          class="mt-3"
                          :href="openRawUrl || undefined"
                          target="_blank"
                          rel="noopener"
                        >
                          <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
                          下载原文件
                        </v-btn>
                      </div>
                      <CodeEditor v-else-if="openPath" v-model="fileDraft" :filename="openPath" @save="saveFile" />
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
                        <div class="usage-num">{{ fmtNum(row.u.turns) }}</div>
                        <div class="t-meta">轮次</div>
                      </div>
                      <div class="usage-cell">
                        <div class="usage-num">{{ fmtNum(row.u.total_tokens) }}</div>
                        <div class="t-meta">总 token</div>
                      </div>
                      <div class="usage-cell">
                        <div class="usage-num">{{ fmtNum(row.u.input_tokens) }}</div>
                        <div class="t-meta">输入</div>
                      </div>
                      <div class="usage-cell">
                        <div class="usage-num">{{ fmtNum(row.u.output_tokens) }}</div>
                        <div class="t-meta">输出</div>
                      </div>
                      <div class="usage-cell">
                        <div class="usage-num" :title="costNote(row.u)">{{ costLabel(row.u) }}</div>
                        <div class="t-meta">费用</div>
                      </div>
                    </div>
                    <div v-if="row.u && costNote(row.u)" class="t-meta mt-1">
                      {{ costNote(row.u) }}
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
                  <!-- The app now rides the backend's reverse proxy, so it is on
                   OUR origin: allow-same-origin would hand whatever the agent is
                   serving our localStorage (session token) and our API cookies.
                   Opaque origin only — same posture as the file artifact below. -->
                  <iframe class="preview-frame" :src="previewAppUrl ?? undefined" sandbox="allow-scripts allow-forms" />
                </div>
                <div v-else-if="previewError" class="text-center text-medium-emphasis py-8">
                  <v-icon size="32" class="text-error mb-2">mdi-alert-circle-outline</v-icon>
                  <div>预览加载失败</div>
                  <div class="text-caption mt-1">后端没能返回这个话题的预览：{{ previewError }}</div>
                </div>
                <div v-else-if="previewReadError" class="text-center text-medium-emphasis py-8">
                  <v-icon size="32" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
                  <div>指定的产物读不到</div>
                  <div class="text-caption mt-1">
                    芝士指定了 {{ previewNamedPath || '一个文件' }}，但它现在读不出来：{{ previewReadError }}
                  </div>
                </div>
                <div v-else-if="previewNamed && previewAppNote" class="text-center text-medium-emphasis py-8">
                  <v-icon size="32" class="text-disabled mb-2">mdi-lan-disconnect</v-icon>
                  <div>应用暂时不在线</div>
                  <div v-if="previewContainerUp" class="text-caption mt-1">
                    容器还在，但约定端口上没有服务在应答——芝士声明过的那个 dev server 大概已经退出了，再 @
                    它一次拉起来。
                  </div>
                  <div v-else class="text-caption mt-1">
                    芝士声明过一个运行中的应用，但它的容器当前没在跑——再 @ 它一次即可拉起。
                  </div>
                </div>
                <div v-else-if="previewFile" class="preview-wrap">
                  <div class="preview-bar text-caption px-3 pt-2">
                    <span class="text-medium-emphasis">{{ previewFile.path }}</span>
                    <v-chip v-if="previewNamed" size="x-small" color="primary" variant="tonal" class="ms-2"
                      >芝士指定</v-chip
                    >
                    <v-chip size="x-small" variant="outlined" class="ms-1">
                      {{ previewMime }}
                    </v-chip>
                  </div>
                  <!-- allow-scripts WITHOUT allow-same-origin (Claude Artifacts
                   posture): interactive artifacts run their JS, but in an
                   opaque origin that cannot touch the platform page. -->
                  <iframe class="preview-frame" :srcdoc="previewFile.content ?? ''" sandbox="allow-scripts" />
                </div>
                <div v-else class="text-center text-medium-emphasis py-8">
                  <v-icon size="32" class="text-disabled mb-2">mdi-eye-off-outline</v-icon>
                  <div>芝士还没有指定预览</div>
                  <div class="text-caption mt-1">
                    它做出网页 / 图表等可看的产物时，会把成果放到这里。 单文件产物直接渲染；整个应用（如 Vue
                    工程）走"运行环境预览"——芝士把 dev server 跑起来再声明一次即可。
                  </div>
                </div>
              </template>
            </div>
          </aside>
        </transition>
      </div>
      <!-- /.doc-stage -->

      <!-- 全屏预览 overlay: same artifact, workspace-covering (Esc / ✕ closes). -->
      <Teleport to="body">
        <!-- Esc is handled by a window listener (onPreviewFullKeydown) — a div
         never has focus, so a @keydown on it can never fire. -->
        <div v-if="previewFull" class="preview-full">
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
            <v-btn icon="mdi-close" size="small" variant="text" class="c-muted" @click="previewFull = false" />
          </div>
          <iframe
            v-if="previewAppUrl"
            class="preview-full__frame"
            :src="previewAppUrl"
            sandbox="allow-scripts allow-forms"
          />
          <iframe
            v-else-if="previewFile"
            class="preview-full__frame"
            :srcdoc="previewFile.content ?? ''"
            sandbox="allow-scripts"
          />
        </div>
      </Teleport>

      <!-- 军规 1: manual save of a lossy-loaded doc needs explicit consent. -->
      <v-dialog v-model="lossyConfirmOpen" max-width="440">
        <v-card rounded="lg">
          <v-card-title class="text-subtitle-1 d-flex align-center ga-2">
            <v-icon size="20" color="warning">mdi-alert-outline</v-icon>
            确认覆盖保存？
          </v-card-title>
          <v-card-text class="text-body-2 pt-0">
            此文档包含可视化编辑器暂不完全支持的语法。直接保存会按编辑器的理解重写文件，
            不支持的格式将丢失。用源码模式编辑可以完整保留原文——你刚才的改动会被暂存， 切过去之后可以一键取回。
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn size="small" variant="text" @click="lossyConfirmOpen = false"> 取消 </v-btn>
            <v-btn size="small" variant="tonal" color="primary" @click="enterSourceMode()"> 用源码模式 </v-btn>
            <v-btn size="small" variant="flat" color="warning" @click="confirmLossySave"> 仍要保存 </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

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
/* 文档里的 @/话题 chip：和聊天同一视觉词汇，可点。 */
.doc-editor :deep(.mention) {
  color: rgb(var(--v-theme-primary));
  background: var(--fill);
  border-radius: 4px;
  padding: 0 3px;
  font-weight: 500;
  cursor: pointer;
}
/* @person handle reads as a link: persistent accent underline. File/topic
   refs (📄/#) keep their chip look and only underline on hover, below. */
.doc-editor :deep(.mention:not(.file-ref):not(.topic-ref)) {
  text-decoration: underline;
  text-underline-offset: 2px;
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
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
@media (prefers-reduced-motion: reduce) {
  .site-act__dot--live {
    animation: none;
  }
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
/* Collapsed long entry. The height comes from SITE_CLAMP_LINES via a bound
   custom property rather than a literal here: the template asks that same
   module whether to render the 展开 button, so if the two drift an entry gets
   clamped with no way out of the clamp. */
.site-msg__raw--clamped {
  display: -webkit-box;
  -webkit-line-clamp: var(--site-clamp-lines);
  line-clamp: var(--site-clamp-lines);
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.site-msg__more {
  margin-top: 4px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--faint);
  cursor: pointer;
}
.site-msg__more:hover {
  color: var(--text);
  text-decoration: underline;
}
/* Transparent scrim: an outside click dismisses the floating panel. */
.tool-scrim {
  position: absolute;
  inset: 0;
  z-index: 5;
}
.tool-slide-enter-active,
.tool-slide-leave-active {
  transition:
    transform 0.18s ease,
    opacity 0.18s ease;
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
.file-bar__ro {
  font-size: 0.72rem;
  color: var(--muted);
  border: 1px solid rgba(var(--v-border-color), 0.6);
  border-radius: 4px;
  padding: 1px 6px;
  flex: 0 0 auto;
}
.file-conflict {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  padding: 6px 8px;
  font-size: 0.76rem;
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.07);
  border-bottom: 1px solid rgba(var(--v-theme-error), 0.25);
  flex: 0 0 auto;
}
.file-conflict__text {
  flex: 1 1 200px;
  min-width: 0;
}
.file-blob {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 16px;
  text-align: center;
}
.file-blob__title {
  font-size: 0.85rem;
  color: var(--text);
}
.file-blob__note {
  font-size: 0.75rem;
  color: var(--muted);
  margin-top: 4px;
  word-break: break-all;
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
/* 实时终端(只读): the embedded ttyd pane fills the 现场 drawer height. */
.term-wrap {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.term-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  border-bottom: 1px solid var(--border, rgba(0, 0, 0, 0.08));
}
.term-bar__dot {
  color: #3fb950;
  font-size: 10px;
}
.term-frame {
  flex: 1 1 auto;
  width: 100%;
  border: none;
  background: #000;
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
  background: var(--canvas);
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
  transition:
    background 0.12s ease,
    color 0.12s ease;
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
  background: conic-gradient(var(--line-2) 0 25%, transparent 0 50%, var(--line-2) 0 75%, transparent 0) 0 0 / 16px 16px; /* checkerboard so transparency reads */
}
.file-image-view img {
  max-width: 95%;
  max-height: 95%;
  object-fit: contain;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.15);
  background: white;
}

/* Feishu-style comment anchor: quiet dashed amber underline; hover lifts. */
.doc-editor :deep(.comment-anchor) {
  border-bottom: 1.5px dashed rgba(var(--v-theme-primary), 0.55);
  padding-bottom: 1px;
  cursor: pointer;
}
.doc-editor :deep(.comment-anchor:hover) {
  background: rgba(var(--v-theme-primary), 0.08);
}
.comment-card--pulse {
  animation: comment-pulse 1.5s ease;
}
@keyframes comment-pulse {
  0% {
    background: rgba(var(--v-theme-primary), 0.16);
  }
  100% {
    background: transparent;
  }
}

.doc-error-toast {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  z-index: 30;
  max-width: min(560px, calc(100% - 32px));
  overflow-wrap: anywhere;
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
  transition:
    background 0.15s,
    box-shadow 0.15s;
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
/* Anchored comment's quote chip: the message-quote visual language (amber left
   bar over a faint amber ground). Click → scroll + flash the paragraph. */
.doc-comments__chip {
  display: block;
  max-width: 100%;
  text-align: left;
  border: none;
  border-left: 2px solid var(--accent, #f57f17);
  background: rgba(245, 127, 23, 0.06);
  border-radius: 0 6px 6px 0;
  padding: 3px 8px;
  margin: 2px 0 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--muted);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: background 0.15s;
}
.doc-comments__chip:hover {
  background: rgba(245, 127, 23, 0.13);
}
/* The anchor node no longer exists — the paragraph was edited away. */
.doc-comments__stale {
  font-size: 12px;
  color: var(--faint);
  margin: 2px 0 4px;
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
  transition:
    border-color 0.15s,
    background 0.15s;
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
/* ---- Document typography: Feishu-quiet rhythm. Heading sizes step down
   evenly; vertical space leans UP (more before than after) so headings bind
   to their section. ---- */
.doc-editor :deep(h1) {
  font-size: 1.6em;
  font-weight: 650;
  letter-spacing: -0.015em;
  line-height: 1.35;
  margin: 1.1em 0 0.4em;
}
.doc-editor :deep(h2) {
  font-size: 1.32em;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.4;
  margin: 1.15em 0 0.35em;
}
.doc-editor :deep(h3) {
  font-size: 1.13em;
  font-weight: 600;
  line-height: 1.45;
  margin: 1em 0 0.3em;
}
.doc-editor :deep(h4) {
  font-size: 1em;
  font-weight: 600;
  line-height: 1.5;
  margin: 0.9em 0 0.25em;
  color: var(--ink);
}
/* The doc starts flush: no phantom gap above a leading heading. */
.doc-editor :deep(.doc-prose > :first-child) {
  margin-top: 0;
}
.doc-editor :deep(p) {
  margin: 0 0 0.75em;
}
.doc-editor :deep(ul),
.doc-editor :deep(ol) {
  margin: 0.4em 0 0.75em;
  padding-left: 1.5em;
}
.doc-editor :deep(li) {
  margin: 0.25em 0;
}
.doc-editor :deep(li::marker) {
  color: var(--muted);
}
.doc-editor :deep(li p) {
  margin: 0;
}
.doc-editor :deep(strong) {
  font-weight: 600;
}
/* 任务列表 (GFM `- [ ]`): checkbox row, marker-less. Checked items fade —
   done work goes quiet, not struck through. */
.doc-editor :deep(ul[data-type='taskList']) {
  list-style: none;
  padding-left: 0.2em;
}
.doc-editor :deep(ul[data-type='taskList'] li) {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.doc-editor :deep(ul[data-type='taskList'] li > label) {
  flex: 0 0 auto;
  user-select: none;
}
.doc-editor :deep(ul[data-type='taskList'] li > div) {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-editor :deep(ul[data-type='taskList'] input[type='checkbox']) {
  width: 15px;
  height: 15px;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
  vertical-align: middle;
  margin: 0;
}
.readonly .doc-editor :deep(ul[data-type='taskList'] input[type='checkbox']) {
  cursor: default;
}
.doc-editor :deep(ul[data-type='taskList'] li[data-checked='true'] > div) {
  color: var(--muted);
}
/* Nested task lists indent under the checkbox column. */
.doc-editor :deep(ul[data-type='taskList'] ul[data-type='taskList']) {
  padding-left: 1.6em;
  margin: 0.25em 0 0;
}
.doc-editor :deep(blockquote) {
  margin: 0.7em 0;
  padding: 6px 14px;
  border-left: 3px solid color-mix(in srgb, var(--accent) 55%, transparent);
  border-radius: 0 6px 6px 0;
  background: color-mix(in srgb, var(--accent) 4%, transparent);
  color: rgba(var(--v-theme-on-surface), 0.72);
}
.doc-editor :deep(blockquote blockquote) {
  margin: 0.4em 0;
  background: transparent;
}
.doc-editor :deep(blockquote p:last-child) {
  margin-bottom: 0;
}
.doc-editor :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: 4px;
  font-size: 0.87em;
}
/* 代码块: light ground + hairline, language tag in the top-right corner
   (hidden while hovered — the copy button takes that spot). */
.doc-editor :deep(pre) {
  position: relative;
  background: var(--canvas);
  border: 1px solid var(--line-2, #ececec);
  padding: 13px 15px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.7em 0;
  font-size: 0.855em;
  line-height: 1.6;
}
/* The static corner tag yields whenever the interactive code bar is up —
   two things must never occupy the corner at once. The bar anchors on the
   same spot, so the swap reads as the tag becoming interactive. */
.doc-editor-wrap:has(.doc-codebar) .doc-editor :deep(pre[data-language])::before {
  opacity: 0;
}
.doc-editor :deep(pre[data-language])::before {
  content: attr(data-language);
  position: absolute;
  top: 5px;
  right: 10px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--faint);
  text-transform: lowercase;
  pointer-events: none;
  transition: opacity 0.12s ease;
}
.doc-editor :deep(pre:hover)::before {
  opacity: 0;
}
.doc-editor :deep(pre) code {
  background: none;
  padding: 0;
  font-size: inherit;
}
/* lowlight token colors — same palette as CodeEditor's cheesex-light Monaco
   theme (light ground, low saturation), so 文档里的代码和文件编辑器一个气质. */
.doc-editor :deep(.hljs-comment),
.doc-editor :deep(.hljs-quote) {
  color: #8a8f98;
  font-style: italic;
}
.doc-editor :deep(.hljs-keyword),
.doc-editor :deep(.hljs-selector-tag),
.doc-editor :deep(.hljs-literal),
.doc-editor :deep(.hljs-doctag),
.doc-editor :deep(.hljs-meta) {
  color: #0b5cad;
}
.doc-editor :deep(.hljs-string),
.doc-editor :deep(.hljs-regexp),
.doc-editor :deep(.hljs-addition) {
  color: #a8471c;
}
.doc-editor :deep(.hljs-number),
.doc-editor :deep(.hljs-symbol),
.doc-editor :deep(.hljs-bullet) {
  color: #0a7a52;
}
.doc-editor :deep(.hljs-title),
.doc-editor :deep(.hljs-section),
.doc-editor :deep(.hljs-name),
.doc-editor :deep(.hljs-function) {
  color: #8a6d1b;
}
.doc-editor :deep(.hljs-type),
.doc-editor :deep(.hljs-class),
.doc-editor :deep(.hljs-built_in),
.doc-editor :deep(.hljs-attr),
.doc-editor :deep(.hljs-attribute),
.doc-editor :deep(.hljs-variable),
.doc-editor :deep(.hljs-template-variable) {
  color: #267f99;
}
.doc-editor :deep(.hljs-deletion) {
  color: #b3403a;
}
.doc-editor :deep(.hljs-emphasis) {
  font-style: italic;
}
.doc-editor :deep(.hljs-strong) {
  font-weight: 600;
}
.doc-editor :deep(hr) {
  border: none;
  border-top: 1px solid var(--line-2, #ececec);
  margin: 1.6em 0;
}
/* 链接: 主题琥珀 ink, quiet until hover. */
.doc-editor :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
  cursor: pointer;
}
.doc-editor :deep(a:hover) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
/* 图片: soft corners, never wider than the column. */
.doc-editor :deep(img) {
  max-width: 100%;
  border-radius: 8px;
  display: block;
  margin: 0.6em 0;
}
.doc-editor :deep(img.ProseMirror-selectednode) {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
/* Table rows breathe on hover (body only, not the header). */
.doc-editor :deep(.doc-prose tbody tr:hover td) {
  background: color-mix(in srgb, var(--accent) 3%, transparent);
}

/* ---- 军规 1 UI ---- */
/* Lossy-load banner: amber, quiet, right above the doc. */
.doc-lossy-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  max-width: 720px;
  margin: 0 auto 16px;
  padding: 9px 12px;
  border-radius: 8px;
  border: 1px solid color-mix(in srgb, var(--accent) 38%, transparent);
  background: color-mix(in srgb, var(--accent) 7%, var(--surface));
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text);
}
.doc-lossy-banner__icon {
  color: var(--accent);
  margin-top: 2px;
}
.doc-lossy-banner__text {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-lossy-banner__btn {
  flex: 0 0 auto;
  border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
  background: var(--surface);
  color: var(--accent-ink);
  border-radius: 6px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s ease;
}
.doc-lossy-banner__btn:hover {
  background: color-mix(in srgb, var(--accent) 10%, var(--surface));
}
/* Header status for the paused state — an honest, quiet warning, not the
   「编辑中…」 that used to impersonate a save in progress. */
.doc-status-paused {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--warn);
  cursor: default;
}

/* 军规 1 notices: content held aside (stash) or in conflict. Full-width, above
   the stage, so they follow the user across 可视化 ⇄ 源码. */
.doc-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line-2);
  background: color-mix(in srgb, var(--warn) 8%, var(--surface));
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text);
}
.doc-notice--conflict {
  background: color-mix(in srgb, var(--danger) 7%, var(--surface));
}
.doc-notice__icon {
  flex: 0 0 auto;
  color: var(--warn);
}
.doc-notice--conflict .doc-notice__icon {
  color: var(--danger);
}
.doc-notice__text {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-notice__btn {
  flex: 0 0 auto;
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--text);
  border-radius: 6px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s ease;
}
.doc-notice__btn:hover {
  background: color-mix(in srgb, var(--text) 6%, var(--surface));
}
.doc-notice__btn--quiet {
  border-color: transparent;
  background: transparent;
  color: var(--muted);
}

/* 源码模式: Monaco fills the stage (it scrolls itself). */
.doc-source {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.doc-codebar {
  position: absolute;
  z-index: 6;
  transform: translateX(-100%);
  display: flex;
  align-items: center;
  gap: 4px;
}
.doc-codelang {
  position: relative;
}
.doc-codelang__chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: 1px solid var(--line-2);
  background: var(--surface);
  border-radius: 6px;
  padding: 2px 7px;
  font-size: 11px;
  color: var(--muted);
  cursor: pointer;
}
.doc-codelang__chip:hover {
  color: var(--ink);
  background: var(--fill);
}
.doc-codelang__menu {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  display: flex;
  flex-direction: column;
  max-height: 260px;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
  padding: 4px;
  min-width: 120px;
}
.doc-codelang__item {
  border: none;
  background: none;
  text-align: left;
  font-size: 12px;
  font-family: ui-monospace, monospace;
  color: var(--ink);
  padding: 5px 9px;
  border-radius: 5px;
  cursor: pointer;
}
.doc-codelang__item:hover {
  background: var(--fill);
}

/* Notion-style slash menu — same visual language as .doc-codelang__menu:
   surface ground, hairline border, radius 8, soft shadow; the active item
   (keyboard or hover) sits on --fill. */
.doc-slash__menu {
  position: absolute;
  z-index: 7;
  display: flex;
  flex-direction: column;
  min-width: 196px;
  max-height: 300px;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
  padding: 4px;
}
.doc-slash__item {
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: none;
  text-align: left;
  font-size: 13px;
  color: var(--ink);
  padding: 6px 9px;
  border-radius: 5px;
  cursor: pointer;
  white-space: nowrap;
}
.doc-slash__item--active {
  background: var(--fill);
}
.doc-slash__icon {
  color: var(--muted);
  flex: 0 0 auto;
}
.doc-slash__label {
  flex: 1 1 auto;
}
.doc-slash__hint {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--faint);
}

/* Code-block copy button: the chat hover-action language — surface ground,
   hairline border, muted icon, only present while hovering the block. */
.doc-codecopy {
  z-index: 5;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 24px;
  border: 1px solid var(--line-2, #ececec);
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(16, 18, 22, 0.08);
  transition:
    color 0.12s ease,
    border-color 0.12s ease;
}
.doc-codecopy:hover {
  color: var(--ink);
  border-color: var(--line);
}
.doc-codecopy--done {
  color: #35b37e;
  border-color: color-mix(in srgb, #35b37e 40%, transparent);
}
</style>
