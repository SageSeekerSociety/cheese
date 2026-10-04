<script setup lang="ts">
// 压在正文上、跟着正文走的那几块：选中文字后的浮条（doc/DocBubble.vue）、
// 右键菜单式的 slash 菜单浮层、
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
import type { Selection } from '@tiptap/pm/state'
import type { CommentSpot } from '../../../lib/docCommentSpots'
import type { DocLinkTarget } from '../../../lib/docLinks'
import type { SlashItem } from '../../../lib/docSlashMenu'

import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import { TextSelection } from '@tiptap/pm/state'
import { CellSelection } from '@tiptap/pm/tables'

import { useFocusReturn } from '@/composables/useFocusReturn'

import { BUBBLE_META } from '../../../lib/docBubble'
import { spotAt } from '../../../lib/docCommentSpots'
import { captureNewDocLink } from '../../../lib/docLinks'
import { BLOCK_ITEMS, blockKeyOf } from '../../../lib/docSlashMenu'
import { statusAt } from '../../../lib/docStatus'

import DocBubble from './DocBubble.vue'
import DocKeyboardBar from './DocKeyboardBar.vue'
import DocSlashMenu from './DocSlashMenu.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    editor?: CoreEditor | null
    /** 能不能改。只读时浮条上没有格式，语言选择器、块手柄都不出场。 */
    editable: boolean
    topicId: string | null
    /** 上面那把 slash 菜单现在开在哪、停在第几行；null = 关着。 */
    slashMenu?: { items: SlashItem[]; index: number; top: number; left: number } | null
    /** 父层每收到一次正文区的滚动就加一：滚动时收起代码块工具条。 */
    scrollTick?: number
    /** 项目 AI 队友的名字和 handle：浮条上的那个入口是它。 */
    agentName: string
    agentHandle?: string | null
    /** 浮条上给不给 AI 队友（认得出它，问它它才收得到）。 */
    canAgent?: boolean
    /** 能写评论（归档话题的文档不能）。 */
    canComment?: boolean
  }>(),
  { editor: null, slashMenu: null, scrollTick: 0, agentHandle: null, canAgent: false, canComment: true }
)

const emit = defineEmits<{
  /** 选中一段正文点了「评论」：锚点和引文都算好了，去开写评论的框。 */
  (e: 'open-comment', payload: CommentSpot): void
  /** 选中一段正文点了 AI 队友：选区、锚点和引文。 */
  (e: 'agent', payload: CommentSpot): void
  /** 当场要说的失败（目前只有复制代码失败）。 */
  (e: 'error', message: string): void
  /** slash 菜单里挑了一项（键盘回车走的是上面那条路，这里只有鼠标）。 */
  (e: 'pick', item: SlashItem): void
  /** 鼠标停到了 slash 菜单的第几项。 */
  (e: 'hover', index: number): void
  /** ＋ 手柄往新块里种了一个「/」：菜单关掉时若是它种的那一个，要收回去。 */
  (e: 'planted'): void
  (e: 'open-link', target: DocLinkTarget): void
}>()

// B4 Feishu-style: a floating "评论" button that appears over a text selection in
// the doc. Clicking it opens the comment composer anchored to the selected
// paragraph, with the selected span quoted. Positioned inside .doc-editor-wrap
// (like the other overlays) so it scrolls with the content.
interface CommentCta {
  top: number
  left: number
  quote: string
  editor: CoreEditor
  doc: PMNode
  selection: Selection
  topicId: string
  /** The caret sits in a status tag: the bar offers its kinds, over the tag. */
  status?: { from: number; to: number }
}
const commentCta = shallowRef<CommentCta | null>(null)
// 手指点的屏幕上（手机、平板），能改时浮条换成键盘上方的那一条：系统自己的选区菜单会
// 压在浮条上。
const TOUCH = '(hover: none) and (pointer: coarse)'
const touchQuery = typeof window.matchMedia === 'function' ? window.matchMedia(TOUCH) : null
const touch = ref(touchQuery?.matches ?? false)
const onTouchChange = (e: MediaQueryListEvent) => (touch.value = e.matches)
touchQuery?.addEventListener?.('change', onTouchChange)
onBeforeUnmount(() => touchQuery?.removeEventListener?.('change', onTouchChange))
const toolbar = ref<HTMLElement | null>(null)
let dismissed: { editor: CoreEditor; doc: PMNode; selection: Selection } | null = null
let frame = 0
let disposed = false
let bound: CoreEditor | null = null
let observer: ResizeObserver | null = null
function wrapOf(ed: CoreEditor) {
  return ed.view.dom.closest<HTMLElement>('.doc-editor-wrap')
}
function sameSelection(ed: CoreEditor) {
  return dismissed?.editor === ed && dismissed.doc === ed.state.doc && dismissed.selection.eq(ed.state.selection)
}
function positionCta() {
  const cta = commentCta.value
  if (!cta || disposed) return
  const ed = cta.editor
  if (ed.isDestroyed || ed.state.doc !== cta.doc || props.topicId !== cta.topicId) {
    commentCta.value = null
    return
  }
  const wrap = wrapOf(ed),
    body = wrap?.closest<HTMLElement>('.doc-body') ?? wrap
  if (!wrap || !body) {
    commentCta.value = null
    return
  }
  const wr = wrap.getBoundingClientRect(),
    br = body.getBoundingClientRect()
  const sidebar = wrap.closest('.doc-reading')?.querySelector<HTMLElement>('[data-comments-panel]')
  const sr = sidebar && getComputedStyle(sidebar).display !== 'none' ? sidebar.getBoundingClientRect() : null
  const left = Math.max(0, br.left),
    top = Math.max(0, br.top)
  const right = Math.min(window.innerWidth, br.right, sr && sr.width > 0 ? sr.left : br.right)
  const bottom = Math.min(window.innerHeight, br.bottom)
  const tw = toolbar.value?.offsetWidth || 96,
    th = toolbar.value?.offsetHeight || 32
  if (right - left < tw + 8 || bottom - top < th + 8) {
    commentCta.value = null
    return
  }
  try {
    const sel = cta.selection
    const a = ed.view.coordsAtPos(
      cta.status ? cta.status.from : sel instanceof CellSelection ? sel.$anchorCell.pos + 1 : sel.from
    )
    const h = ed.view.coordsAtPos(
      cta.status ? cta.status.to : sel instanceof CellSelection ? sel.$headCell.pos + 1 : sel.to
    )
    if (Math.max(a.bottom, h.bottom) < top || Math.min(a.top, h.top) > bottom) {
      commentCta.value = null
      return
    }
    const x = Math.max(left + 4, Math.min((a.left + h.right) / 2 - tw / 2, right - tw - 4))
    const above = Math.min(a.top, h.top) - th - 6
    const y = Math.max(top + 4, Math.min(above >= top + 4 ? above : Math.max(a.bottom, h.bottom) + 6, bottom - th - 4))
    commentCta.value = { ...cta, left: x - wr.left, top: y - wr.top }
  } catch {
    commentCta.value = null
  }
}
function schedulePosition() {
  if (frame || disposed) return
  frame = requestAnimationFrame(() => {
    frame = 0
    positionCta()
  })
}
function updateCommentCta(ed: CoreEditor) {
  const sel = ed.state.selection
  if (sel.empty && props.topicId && !sameSelection(ed) && props.editable && ed.isEditable && !touch.value) {
    const status = statusAt(ed.state, sel.from)
    if (status) {
      commentCta.value = {
        top: 0,
        left: 0,
        quote: '',
        editor: ed,
        doc: ed.state.doc,
        selection: sel,
        topicId: props.topicId,
        status,
      }
      positionCta()
      schedulePosition()
      return
    }
  }
  if (sel.empty || !props.topicId || sameSelection(ed)) {
    commentCta.value = null
    return
  }
  let quote: string
  if (sel instanceof CellSelection) {
    const parts: string[] = []
    sel.forEachCell((cell) => {
      const text = cell.textContent.trim()
      if (text) parts.push(text)
    })
    quote = parts.join(' ')
  } else {
    if (!(sel instanceof TextSelection)) {
      commentCta.value = null
      return
    }
    quote = ed.state.doc.textBetween(sel.from, sel.to, ' ').trim()
  }
  if (!quote) {
    commentCta.value = null
    return
  }
  dismissed = null
  commentCta.value = {
    top: 0,
    left: 0,
    quote,
    editor: ed,
    doc: ed.state.doc,
    selection: sel,
    topicId: props.topicId,
  }
  positionCta()
  schedulePosition()
}
const onSelectionUpdate = ({ editor }: { editor: CoreEditor }) => updateCommentCta(editor)
const onTransaction = ({
  editor,
  transaction,
}: {
  editor: CoreEditor
  transaction: { docChanged: boolean; getMeta: (key: string) => unknown }
}) => {
  if (!transaction.docChanged) return
  // 浮条自己改的格式：选的还是那一段，浮条留着，只换成新的这一版正文。
  if (transaction.getMeta(BUBBLE_META)) updateCommentCta(editor)
  else onEdited()
}
function escapeSelection(e: KeyboardEvent) {
  if (
    e.key !== 'Escape' ||
    e.defaultPrevented ||
    e.isComposing ||
    !commentCta.value ||
    codeLangOpen.value ||
    props.slashMenu
  )
    return
  const wrap = bound && wrapOf(bound)
  if (!(e.target instanceof Node) || !wrap?.contains(e.target)) return
  dismissed = { editor: commentCta.value.editor, doc: commentCta.value.doc, selection: commentCta.value.selection }
  commentCta.value = null
  e.preventDefault()
  e.stopPropagation()
}
function bindEditor(ed?: CoreEditor | null) {
  if (bound === ed) return
  bound?.off('selectionUpdate', onSelectionUpdate)
  bound?.off('transaction', onTransaction)
  observer?.disconnect()
  commentCta.value = null
  dismissed = null
  bound = ed ?? null
  if (bound) {
    bound.on('selectionUpdate', onSelectionUpdate)
    bound.on('transaction', onTransaction)
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(schedulePosition)
      const wrap = wrapOf(bound)
      const pane = wrap?.closest('.doc-reading') ?? wrap
      if (pane) observer.observe(pane)
      const body = wrap?.closest('.doc-body')
      if (body) observer.observe(body)
      const sidebar = pane?.querySelector('[data-comments-panel]')
      if (sidebar) observer.observe(sidebar)
    }
  }
}
watch(() => props.editor, bindEditor, { immediate: true, flush: 'post' })
watch(
  () => [props.editable, props.topicId],
  () => {
    commentCta.value = null
    dismissed = null
  },
  { flush: 'sync' }
)
document.addEventListener('scroll', schedulePosition, true)
window.addEventListener('resize', schedulePosition)
document.addEventListener('keydown', escapeSelection, true)
onBeforeUnmount(() => {
  disposed = true
  bound?.off('selectionUpdate', onSelectionUpdate)
  bound?.off('transaction', onTransaction)
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
  document.removeEventListener('scroll', schedulePosition, true)
  window.removeEventListener('resize', schedulePosition)
  document.removeEventListener('keydown', escapeSelection, true)
})
/** 浮条收起，交出选中的那几个字（记成跟着正文走的位置）；中途换了文档、正文变了就是 null。 */
function takeSelection(): CommentSpot | null {
  const cta = commentCta.value
  if (!cta) return null
  dismissed = { editor: cta.editor, doc: cta.doc, selection: cta.selection }
  commentCta.value = null
  if (props.editor !== cta.editor || cta.editor.isDestroyed || cta.editor.state.doc !== cta.doc) return null
  return spotAt(cta.editor, cta.selection.from, cta.selection.to)
}
function commentOnSelection() {
  const spot = takeSelection()
  if (spot) emit('open-comment', spot)
}
function agentOnSelection() {
  const spot = takeSelection()
  if (spot) emit('agent', spot)
}
async function copySelection() {
  const cta = commentCta.value
  if (!cta) return
  try {
    await navigator.clipboard.writeText(cta.editor.state.doc.textBetween(cta.selection.from, cta.selection.to, '\n'))
    dismissed = { editor: cta.editor, doc: cta.doc, selection: cta.selection }
    commentCta.value = null
  } catch {
    emit('error', t('work.room.doc.copyFailed'))
  }
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
  return codeCopyPre?.getAttribute('data-language') || t('work.room.doc.language')
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
    emit('error', t('work.room.doc.copyFailed'))
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

// ---- 行首手柄点一下：这一块换成别的块（和浮条上的「正文 ▾」同一张表），不用先选字。
// 拖它照旧是挪这一块。菜单量着手柄的位置摆在屏幕上，手柄因为鼠标移开而收起时它还在。
const blockMenu = ref<{ pos: number; current: string; top: number; left: number } | null>(null)
function openBlockMenu(e: MouseEvent) {
  const ed = props.editor
  if (!ed || hoverPos.value == null) return
  const node = ed.state.doc.nodeAt(hoverPos.value)
  if (!node) return
  const at = (e.currentTarget as HTMLElement).getBoundingClientRect()
  blockMenu.value = { pos: hoverPos.value, current: blockKeyOf(node), top: at.bottom + 4, left: at.left }
}
function pickBlock(item: SlashItem) {
  const ed = props.editor
  const menu = blockMenu.value
  blockMenu.value = null
  const node = ed && menu ? ed.state.doc.nodeAt(menu.pos) : null
  if (!ed || !menu || !node) return
  item
    .run(
      ed
        .chain()
        .focus()
        .setTextSelection({ from: menu.pos + 1, to: menu.pos + node.nodeSize - 1 })
    )
    .run()
}
function closeBlockMenu(e: Event) {
  if (e instanceof KeyboardEvent && e.key !== 'Escape') return
  if (e.target instanceof Element && e.target.closest('.doc-block-menu')) return
  blockMenu.value = null
}
watch(blockMenu, (open, was) => {
  if (open && !was) {
    document.addEventListener('mousedown', closeBlockMenu, true)
    document.addEventListener('keydown', closeBlockMenu, true)
    document.addEventListener('scroll', closeBlockMenu, true)
  } else if (!open && was) {
    document.removeEventListener('mousedown', closeBlockMenu, true)
    document.removeEventListener('keydown', closeBlockMenu, true)
    document.removeEventListener('scroll', closeBlockMenu, true)
  }
})
onBeforeUnmount(() => (blockMenu.value = null))

// 块手柄菜单关上时把焦点还回手柄。
useFocusReturn(computed(() => !!blockMenu.value))

/** 正文在光标底下换了（人工编辑，或者装进来的一版）：这个按钮指着的段落已经不是
 *  原来那一段了，收回去。装配服务端那一版时上面不会喊这一声。 */
function onEdited() {
  if (props.editor)
    dismissed = { editor: props.editor, doc: props.editor.state.doc, selection: props.editor.state.selection }
  commentCta.value = null
}

function newLink() {
  const cta = commentCta.value
  if (!cta || cta.editor.state.doc !== cta.doc) return
  const target = captureNewDocLink(cta.editor, cta.selection)
  if (target) emit('open-link', target)
}
/** 选中的是正文里的字（一段或跨几段）：块样式能换。 */
function restyle(cta: CommentCta): boolean {
  const sel = cta.selection
  return sel instanceof TextSelection && sel.$from.depth > 0
}
defineExpose({ onHover, onEdited })
</script>

<template>
  <!-- 选中文字后的浮条：贴着选区，跟着正文滚。 -->
  <Transition name="doc-comment-cta">
    <div
      v-if="commentCta && !(touch && editable)"
      ref="toolbar"
      class="doc-comment-cta"
      :style="{ top: `${commentCta.top}px`, left: `${commentCta.left}px` }"
    >
      <DocBubble
        :editor="commentCta.editor"
        :agent-name="agentName"
        :agent-handle="agentHandle"
        :editable="editable && commentCta.editor.isEditable"
        :restyle="restyle(commentCta)"
        :status-only="!!commentCta.status"
        :can-agent="canAgent"
        :can-comment="canComment"
        @agent="agentOnSelection"
        @comment="commentOnSelection"
        @link="newLink"
        @copy="copySelection"
      />
    </div>
  </Transition>
  <!-- 手机上能改时：键盘上方的那一条代替浮条，没选中字时也在。 -->
  <DocKeyboardBar v-if="touch && editable && editor" :editor="editor">
    <DocBubble
      variant="bar"
      :editor="editor"
      :agent-name="agentName"
      :agent-handle="agentHandle"
      :editable="editor.isEditable"
      :can-agent="canAgent"
      :can-comment="canComment"
      :has-selection="!!commentCta"
      @agent="agentOnSelection"
      @comment="commentOnSelection"
      @link="newLink"
      @copy="copySelection"
    />
  </DocKeyboardBar>
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
      :title="codeCopy.done ? t('work.room.doc.copied') : t('work.room.doc.copyCode')"
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
      :title="t('work.room.doc.insertBelow')"
      draggable="false"
      @dragstart.stop.prevent
      @click="addBlockBelow"
    >
      <v-icon size="15">mdi-plus</v-icon>
    </button>
    <span
      class="doc-handle__btn doc-handle__grip"
      role="button"
      aria-haspopup="menu"
      :aria-expanded="!!blockMenu"
      :aria-label="t('work.room.doc.blockHandle')"
      :title="t('work.room.doc.blockHandle')"
      @click="openBlockMenu"
    >
      <v-icon size="15">mdi-drag-vertical</v-icon>
    </span>
  </DragHandle>
  <Teleport to="body">
    <div
      v-if="blockMenu"
      class="doc-block-menu"
      role="menu"
      :aria-label="t('work.room.doc.blockType')"
      :style="{ top: `${blockMenu.top}px`, left: `${blockMenu.left}px` }"
    >
      <button
        v-for="item in BLOCK_ITEMS"
        :key="item.key"
        type="button"
        role="menuitemradio"
        :aria-checked="item.key === blockMenu.current"
        class="doc-block-menu__item"
        @click="pickBlock(item)"
      >
        <v-icon size="16">{{ item.icon }}</v-icon>
        {{ item.label }}
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.doc-comment-cta-enter-active {
  transition:
    opacity 120ms ease-out,
    transform 140ms cubic-bezier(0.2, 0, 0, 1);
}
.doc-comment-cta-leave-active {
  transition: opacity 80ms ease-in;
}
.doc-comment-cta-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.doc-comment-cta-leave-to {
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
  .doc-comment-cta-enter-active,
  .doc-comment-cta-leave-active {
    transition: none;
  }
}
/* 浮条的外框：只管摆在哪儿，样子在 DocBubble 里。 */
.doc-comment-cta {
  position: absolute;
  z-index: var(--z-raised-6);
}

/* 手指没有悬停：手柄出不来也点不准，手机上换格式用键盘上方那一条。 */
@media (hover: none) {
  .doc-handle {
    display: none;
  }
}
/* 点手柄开出的那张：和浮条上的「正文 ▾」一个样子。 */
.doc-block-menu {
  position: fixed;
  z-index: var(--z-overlay);
  display: flex;
  flex-direction: column;
  min-width: 168px;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
  animation: docBlockMenuIn 120ms ease-out;
}
@keyframes docBlockMenuIn {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
}
.doc-block-menu__item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  padding: 0 9px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.doc-block-menu__item:hover,
.doc-block-menu__item[aria-checked='true'] {
  background: var(--fill);
}
.doc-block-menu__item .v-icon {
  color: var(--muted);
}
@media (prefers-reduced-motion: reduce) {
  .doc-block-menu {
    animation: none;
  }
}
/* Feishu-style left gutter block handles — REAL controls, not decoration.
   The DragHandle floats next to the hovered block (positioned by the extension).
   ＋ inserts a block below (click); ⠿ drags to reorder. */
.doc-handle {
  display: flex;
  align-items: center;
  gap: 0;
  /* The DragHandle plugin pins this element's RIGHT edge to the text's left
     edge — without the padding the ⠿ glyph literally touches the first
     character. The whole handle (42px) has to fit in the page's left
     padding (PanelDocView .doc-page), or the scroller clips it. */
  padding-right: 6px;
  /* Nudge down so the 22px buttons center on the ~29px first text line. */
  transform: translateY(3.4px);
}
.doc-handle__btn {
  width: 18px;
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
  z-index: var(--z-raised-6);
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
  z-index: var(--z-raised-5);
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
