<script setup lang="ts">
// 文档 tab: 实况文档本身 —— tiptap 可视化编辑器 + 源码模式 + 常驻评论区。
//
// 难的部分不在这里：编辑/冲突状态机在 lib/docEditState.ts，markdown 保真在
// lib/docMarkdown.ts，两者各有自己的单测。这个组件只把它们接到 refs 和 DOM 上。
import type { ChainedCommands, Editor as CoreEditor } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { SuggestionProps } from '@tiptap/suggestion'
import type { Block, Topic } from '../../cx_types'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Extension } from '@tiptap/core'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { CellSelection } from '@tiptap/pm/tables'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import { Suggestion } from '@tiptap/suggestion'
import { EditorContent, useEditor } from '@tiptap/vue-3'

import { getComments, getDoc, getDocNodes, putDoc, workspaceFileRawUrl } from '../../api'
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
} from '../../lib/docEditState'
import { compareRoundTrip, docExtensions, serializeDoc } from '../../lib/docMarkdown'
import { relTime } from '../../lib/relTime'
import { myHandle } from '../../me'
import CodeEditor from '../CodeEditor.vue'

// The living doc is the core interface (spec §2.2): an AI-maintained markdown
// document the user can also edit ("改文档即指令"). Stored as markdown, so the
// editor reads markdown in (contentType: 'markdown') and serializes markdown out
// (editor.getMarkdown()).
const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // Bumped by the parent on AI activity (turn-done / update_doc tool) so the
    // panel reloads the doc 芝士 just wrote. See TopicView activityTick.
    activityTick: number
    // Project topics (A2): resolve a doc node's upgraded_to_topic_id to the
    // subtopic's title + live status for the in-place live-ref badge.
    topicList?: Topic[]
  }>(),
  { topicList: () => [] }
)

// open-topic (A2): a doc live-ref chip was clicked — the parent navigates to the
// subtopic. open-file: a <&path> chip was clicked — WorkPanel switches to the
// 改动 tab and opens it there (the ONE cross-tab wire, and the only one).
const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
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
  // 图标而不是 🧩：emoji 在不同系统上是彩色位图，尺寸和基线都不跟随字号，混在
  // 正文里显得很脏。用 puzzle 而不是 mdi-source-branch，是因为后者在本组件里
  // 已经代表 Git 标签页和「磁盘版本分叉」提示，一个图标不该同时是三件事。
  // (ProseMirror widget 是裸 DOM，用不了 <v-icon>；@mdi/font 是全局 CSS，
  //  所以这里直接写 mdi 的字体类。)
  const icon = document.createElement('i')
  icon.className = 'mdi mdi-puzzle-outline doc-liveref__icon'
  icon.setAttribute('aria-hidden', 'true')
  const label = document.createElement('span')
  label.className = 'doc-liveref__label'
  label.textContent = sub?.title ?? '子话题'
  const st = document.createElement('span')
  st.className = 'doc-liveref__status'
  st.textContent = statusLabel(status)
  el.append(dot, icon, label, st)
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
defineExpose({ pulse, highlightTurn, refreshComments })

const projectId = computed<string | null>(() => props.topic?.project_id ?? null)

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
    // mdi 图标而不是 📄，理由同 doc-liveref：emoji 是彩色位图，不跟随字号和
    // 前景色。mdi-file-document-outline 是本仓库既有的「文件」图标。
    const icon = document.createElement('i')
    icon.className = 'mdi mdi-file-document-outline file-ref__icon'
    icon.setAttribute('aria-hidden', 'true')
    el.append(icon, document.createTextNode(id.split('/').pop() || id))
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
      // the doc keeps `<&path>` verbatim, the reader sees 「(文件图标) name」.
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
  else if (el.dataset.file) emit('open-file', el.dataset.file)
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

// A <&path> chip opens that file in the 改动 tab's editor. The doc tab does not
// own the file browser, so it asks: WorkPanel switches tabs and calls in.

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
    // 现场 / 改动 / 预览 each drop their own per-topic state — see their
    // components. WorkPanel puts the panel back on this tab.
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
  <!-- 根元素上不要放 Vuetify 的 display 工具类：WorkPanel 用 v-show 切 tab，而
       `.d-flex` 是 display: flex !important，会盖掉 v-show 写进去的 inline
       display: none（见 src/vShowDisplayUtilities.spec.ts）。 -->
  <div class="doc">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>选择一个话题查看文档</div>
      </div>
    </div>

    <template v-else>
      <!-- 文档自己的工具条。保存状态 / 只读 / 源码 只对这个 tab 有意义，所以住在
           这个 tab 里 —— 每个 tab 自带自己的控件，后面四张卡才各改各的文件。 -->
      <div class="doc-bar">
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
          :class="sourceMode ? 'tool-btn--active' : 'c-muted'"
          title="源码模式（直接编辑 markdown 原文）"
          @click="toggleSourceMode"
        >
          源码
        </v-btn>
      </div>
      <!-- 军规 1 notices. Above the stage so they show in BOTH visual and
           source mode — the states they describe survive a mode switch. -->
      <!-- Edits a mode switch could not carry over: held, not dropped. -->
      <div v-if="hasPendingEdits" class="doc-notice">
        <v-icon size="16" class="doc-notice__icon">mdi-content-save-alert-outline</v-icon>
        <div class="doc-notice__text">
          有未保存的改动没有带入当前编辑器，编辑器显示的是磁盘上的版本。改动仍然保留，可以随时取回。
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
                此文档包含编辑器暂不完全支持的语法，可视化编辑保存可能丢失格式。自动保存已暂停，建议用源码模式编辑。
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
              <!-- Real block handles: 拆出子话题、拖动重排、在下方插入一块。
                 Only in edit mode. -->
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
      </div>
      <!-- /.doc-stage -->

      <!-- 军规 1: manual save of a lossy-loaded doc needs explicit consent. -->
      <v-dialog v-model="lossyConfirmOpen" max-width="440">
        <v-card rounded="lg">
          <v-card-title class="text-subtitle-1 d-flex align-center ga-2">
            <v-icon size="20" color="warning">mdi-alert-outline</v-icon>
            确认覆盖保存？
          </v-card-title>
          <v-card-text class="text-body-2 pt-0">
            此文档包含可视化编辑器暂不完全支持的语法。直接保存会按编辑器的理解重写文件，不支持的格式将丢失。
            用源码模式编辑可以完整保留原文，你刚才的改动会被暂存，切过去之后可以一键取回。
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
/* 文档 tab 自己的工具条：右对齐的一条细行（保存状态 / 只读 / 源码）。原来这些
   控件挂在 DocPanel 的 v-toolbar 上，那条 toolbar 同时还是「文档」标题、专注按钮
   和五个抽屉图标的家 —— 现在标题和专注归话题头部，抽屉图标变成了 tab。 */
.doc-bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  min-height: 34px;
  padding: 0 6px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.doc {
  /* In the split workspace the doc is a full white surface that fills the pane —
     not a floating card on a gray canvas (which left gray gutters around it). */
  background: var(--surface);
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
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
  border-radius: var(--radius-sm);
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
  border-radius: var(--radius-sm);
  padding: 0 3px;
  font-weight: 500;
  cursor: pointer;
}
/* @person handle reads as a link: persistent accent underline. File/topic
   refs (file icon / #) keep their chip look and only underline on hover. */
.doc-editor :deep(.mention:not(.file-ref):not(.topic-ref)) {
  text-decoration: underline;
  text-underline-offset: 2px;
}
.doc-editor :deep(.mention:hover) {
  text-decoration: underline;
}
/* 文件 chip 前的 mdi 图标（正文里的 <&path> 装饰，以及评论区的同款 chip）。 */
:deep(.file-ref__icon) {
  margin-right: 3px;
  font-size: 0.92em;
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
  color: rgb(var(--v-theme-on-primary));
  background: rgb(var(--v-theme-primary));
  box-shadow: var(--shadow-2);
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
  border-radius: var(--radius-sm);
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
  border: 1px solid var(--line);
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
  border-radius: var(--radius-sm);
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
  box-shadow: var(--shadow-2);
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
  border-radius: var(--radius-lg);
  font-size: 0.72rem;
  line-height: 1.6;
  white-space: nowrap;
  color: rgb(var(--v-theme-primary));
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 5%, var(--surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  box-shadow: var(--shadow-1);
  cursor: pointer;
  user-select: none;
  transition:
    background 0.15s,
    box-shadow 0.15s;
}
.doc-editor :deep(.doc-liveref:hover) {
  background: rgba(var(--v-theme-primary), 0.1);
  box-shadow: var(--shadow-2);
}
.doc-editor :deep(.doc-liveref__icon) {
  flex: 0 0 auto;
  font-size: 13px;
  line-height: 1;
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
  background: var(--warn); /* 进行中 */
}
.doc-editor :deep(.doc-liveref__dot.is-archived),
.doc-editor :deep(.doc-liveref__dot.is-completed) {
  background: var(--ok); /* 已完成 */
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
  border-top: 1px solid var(--line-2);
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
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
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
  /* 搬迁保留原样。这一对字面色其实站不住脚 —— 它引用的是 avatarColor() 那条
     豁免（算出来的定色底 + 定色墨），但这个 #8a94a3 不是算出来的，就是一个字面
     灰。同一处缺陷在 TopicView 的 .mention-avatar 上已经按 var(--surface) /
     var(--muted) 修过（顺带把对比度从 2.9:1 提到 5.0:1）。这一轮是纯搬迁，不夹带
     修改；这两行留给「现场 + 预览打磨」那张卡。 */
  /* stylelint-disable-next-line color-no-hex -- 见上，搬迁保留，已记入报告 */
  color: #fff;
  /* stylelint-disable-next-line color-no-hex -- 见上，搬迁保留，已记入报告 */
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
  border-left: 2px solid var(--accent);
  background: rgba(var(--v-theme-primary), 0.06);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
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
  background: rgba(var(--v-theme-primary), 0.13);
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
  border: 1px solid var(--line-2);
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
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
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
  border-radius: var(--radius-sm);
  font-size: 0.87em;
}
/* 代码块: light ground + hairline, language tag in the top-right corner
   (hidden while hovered — the copy button takes that spot). */
.doc-editor :deep(pre) {
  position: relative;
  background: var(--canvas);
  border: 1px solid var(--line-2);
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
/* lowlight token colors — the --code-* palette from style.css, which is also
   what CodeEditor's Monaco theme reads back out, so 文档里的代码和文件编辑器
   一个气质 in BOTH themes. Never inline a literal here: the three consumers
   have to move together or the same snippet looks different in each. */
.doc-editor :deep(.hljs-comment),
.doc-editor :deep(.hljs-quote) {
  color: var(--code-comment);
  font-style: italic;
}
.doc-editor :deep(.hljs-keyword),
.doc-editor :deep(.hljs-selector-tag),
.doc-editor :deep(.hljs-literal),
.doc-editor :deep(.hljs-doctag),
.doc-editor :deep(.hljs-meta) {
  color: var(--code-keyword);
}
.doc-editor :deep(.hljs-string),
.doc-editor :deep(.hljs-regexp),
.doc-editor :deep(.hljs-addition) {
  color: var(--code-string);
}
.doc-editor :deep(.hljs-number),
.doc-editor :deep(.hljs-symbol),
.doc-editor :deep(.hljs-bullet) {
  color: var(--code-number);
}
.doc-editor :deep(.hljs-title),
.doc-editor :deep(.hljs-section),
.doc-editor :deep(.hljs-name),
.doc-editor :deep(.hljs-function) {
  color: var(--code-function);
}
.doc-editor :deep(.hljs-type),
.doc-editor :deep(.hljs-class),
.doc-editor :deep(.hljs-built_in),
.doc-editor :deep(.hljs-attr),
.doc-editor :deep(.hljs-attribute),
.doc-editor :deep(.hljs-variable),
.doc-editor :deep(.hljs-template-variable) {
  color: var(--code-type);
}
.doc-editor :deep(.hljs-deletion) {
  color: var(--code-deletion);
}
.doc-editor :deep(.hljs-emphasis) {
  font-style: italic;
}
.doc-editor :deep(.hljs-strong) {
  font-weight: 600;
}
.doc-editor :deep(hr) {
  border: none;
  border-top: 1px solid var(--line-2);
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
  box-shadow: var(--shadow-2);
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
  border-radius: var(--radius-sm);
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
  box-shadow: var(--shadow-2);
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
  border-radius: var(--radius-sm);
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
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
  box-shadow: var(--shadow-1);
  transition:
    color 0.12s ease,
    border-color 0.12s ease;
}
.doc-codecopy:hover {
  color: var(--ink);
  border-color: var(--line);
}
.doc-codecopy--done {
  color: var(--ok-ink);
  border-color: color-mix(in srgb, var(--ok) 40%, transparent);
}
</style>
