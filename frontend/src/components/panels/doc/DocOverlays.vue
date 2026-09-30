<script setup lang="ts">
// 压在正文上、跟着正文走的那几块：选中文字后的「评论」、右键菜单式的 slash 菜单浮层、
// 代码块工具条（语言 + 复制）、块手柄。
//
// 为什么要一行行地算坐标：这几块都不是正文的一部分，而是**贴在正文上的**。它们的位置
// 来自两个只有编辑器那一半才有的东西 —— 编辑器（光标在哪、鼠标底下是哪个 <pre>）和
// `.doc-editor-wrap` 的矩形。ProseMirror 会亲手撤掉任何别人往它可编辑区里加的东西，
// 所以它们只能长在编辑区的**外面**、靠自己算位置。
//
// 这一层不认识取数：编辑器、节点树和「拿一份最新的节点树」都是上面递进来的，动作往上发。
// slash 菜单的**状态**也住在上面（那套建议插件的回调是在建编辑器时接的），这里只负责画。
import type { Editor as CoreEditor } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { Block } from '../../../cx_types'
import type { SlashItem } from '../../../lib/docSlashMenu'

import { onBeforeUnmount, ref, watch } from 'vue'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import { CellSelection } from '@tiptap/pm/tables'

import { contentBlocks } from './docBlocks'
import DocSlashMenu from './DocSlashMenu.vue'

const props = withDefaults(
  defineProps<{
    editor?: CoreEditor | null
    /** 能不能改。只读时评论 CTA、语言选择器、块手柄都不出场。 */
    editable: boolean
    topicId: string | null
    /** 取一份最新的节点树（评论的锚点要知道自己锚在第几段）。 */
    fetchDocNodes: () => Promise<Block[]>
    /** 上面那把 slash 菜单现在开在哪、停在第几行；null = 关着。 */
    slashMenu?: { items: SlashItem[]; index: number; top: number; left: number } | null
    /** 父层每收到一次正文区的滚动就加一：滚动时收起代码块工具条。 */
    scrollTick?: number
  }>(),
  { editor: null, slashMenu: null, scrollTick: 0 }
)

const emit = defineEmits<{
  /** 选中一段正文点了「评论」：锚点和引文都算好了，去开写评论的框。 */
  (e: 'open-comment', payload: { anchorId: string | null; quote: string }): void
  /** 当场要说的失败（目前只有复制代码失败）。 */
  (e: 'error', message: string): void
  /** slash 菜单里挑了一项（键盘回车走的是上面那条路，这里只有鼠标）。 */
  (e: 'pick', item: SlashItem): void
  /** 鼠标停到了 slash 菜单的第几项。 */
  (e: 'hover', index: number): void
  /** ＋ 手柄往新块里种了一个「/」：菜单关掉时若是它种的那一个，要收回去。 */
  (e: 'planted'): void
}>()

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
  if (sel.empty || !props.editable) {
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

// 「有没有选中」只有编辑器知道，所以订阅接在它身上，而不是让上面每次手选都来喊一声。
// 编辑器是建好之后才递进来的（useEditor 在挂载时才建），所以这里跟到它为止。
const onSelectionUpdate = ({ editor }: { editor: CoreEditor }) => updateCommentCta(editor)
let bound: CoreEditor | null = null
function bindEditor(ed?: CoreEditor | null) {
  if (bound === ed) return
  if (bound) bound.off('selectionUpdate', onSelectionUpdate)
  bound = ed ?? null
  if (bound) bound.on('selectionUpdate', onSelectionUpdate)
}
watch(() => props.editor, bindEditor, { immediate: true })
onBeforeUnmount(() => {
  if (bound) bound.off('selectionUpdate', onSelectionUpdate)
})

async function commentOnSelection() {
  const ed = props.editor
  const cta = commentCta.value
  if (!ed || !props.topicId || !cta) return
  const nodes = await props.fetchDocNodes()
  // Same filler-tolerant alignment as split/highlight. Falls back to a
  // whole-doc comment if the structure can't be mapped.
  const anchor =
    cta.nodeIndex >= nodes.length || nodes.length !== contentBlocks().length ? null : nodes[cta.nodeIndex].id
  commentCta.value = null
  emit('open-comment', { anchorId: anchor, quote: cta.quote })
}

// ---- Code block copy (hover, like the chat's quiet .im-act buttons). The
// button is an overlay OUTSIDE the editable DOM (ProseMirror reverts foreign
// children), positioned over the hovered <pre>'s top-right corner. ----
const codeCopy = ref<{ top: number; right: number; done: boolean } | null>(null)
let codeCopyPre: HTMLElement | null = null

/** 正文区上的鼠标经过。接在 `.doc-editor-wrap` 上（正文那一半替我们挂），因为它
 *  是这块浮层唯一量得到的坐标系 —— 鼠标进不了它自己的那几个按钮以外的任何地方。 */
function onHover(e: MouseEvent) {
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

// The doc scrolls under the toolbar, so it must not stay pinned to a <pre> that
// has moved away. The page bumps scrollTick; here it just closes.
watch(
  () => props.scrollTick,
  () => {
    codeCopy.value = null
  }
)

function currentCodeLang(): string {
  return codeCopyPre?.getAttribute('data-language') || '语言'
}

function setCodeBlockLang(lang: string) {
  codeLangOpen.value = false
  const ed = props.editor
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
    emit('error', '复制失败')
  }
}

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
  const ed = props.editor
  if (!ed || hoverPos.value == null) return
  // Notion behaviour: ＋ = "在此块下方插入新块并问它是什么" — a VISIBLE "/"
  // typed into the new block arms the same slash suggestion typing would
  // (the slash is real content; typing filters; onSlashExit removes an
  // orphaned one). Nuance Notion gets right: if the hovered block is ALREADY
  // an empty paragraph, ask in place — spawning another blank line below an
  // empty line reads as a bug.
  emit('planted')
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

/** 正文在光标底下换了（人工编辑，或者装进来的一版）：这个按钮指着的段落已经不是
 *  原来那一段了，收回去。装配服务端那一版时上面不会喊这一声。 */
function onEdited() {
  commentCta.value = null
}

defineExpose({ onHover, onEdited })
</script>

<template>
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
  <DocSlashMenu
    v-if="slashMenu"
    :items="slashMenu.items"
    :index="slashMenu.index"
    :top="slashMenu.top"
    :left="slashMenu.left"
    @pick="emit('pick', $event)"
    @hover="emit('hover', $event)"
  />
  <!-- Code-block hover toolbar: ONE right-anchored flex bar
     ([language ∨][copy]) growing leftward — the two controls can
     no longer overlap however long the language name gets. -->
  <div v-if="codeCopy" class="doc-codebar" :style="{ top: `${codeCopy.top}px`, left: `${codeCopy.right}px` }">
    <div v-if="editable" class="doc-codelang">
      <button type="button" class="doc-codelang__chip" @click="codeLangOpen = !codeLangOpen">
        {{ currentCodeLang() }}
        <v-icon size="12">mdi-chevron-down</v-icon>
      </button>
      <div v-if="codeLangOpen" class="doc-codelang__menu">
        <button v-for="l in CODE_LANGS" :key="l" type="button" class="doc-codelang__item" @click="setCodeBlockLang(l)">
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
  <!-- A2 in-place live-refs are ProseMirror widget decorations now — rendered in
     the document flow at the end of their paragraph by the LiveRefBadges
     extension (no overlay, no cursor dead zone). Clicks are delegated through
     the doc surface's onDocClick. -->
  <!-- Real block handles: 拆出子话题、拖动重排、在下方插入一块。
     Only in edit mode. -->
  <DragHandle v-if="editor && editable" :editor="editor" :on-node-change="onDocNodeChange" class="doc-handle">
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
</template>

<style scoped>
/* B4 Feishu-style: floating "评论" CTA over a text selection. */
.doc-comment-cta {
  position: absolute;
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 10px;
  border-radius: 8px;
  font-size: 12px;
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
  font-size: 12px;
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
  font-size: 13px;
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
