<script setup lang="ts">
// 文档正文的编辑器本身 —— tiptap 实例、三种装饰、以及「装上服务端那一版 / 把现在这一
// 版交出去」。
//
// 为什么单独一个组件：正文的样式里全是 `:deep()`，而 `:deep()` 只有 .vue 的
// `<style scoped>` 才被 stylelint 认（放进独立 .css 会红）——所以画正文的那一半必须是
// 一个 .vue。它同时是唯一拿着编辑器的地方，取数那一半只通过两个口子跟它说话：
// `installMarkdown`（装进去）和 `serializeVisual`（拿出来），判据留在取数那一半。
//
// 压在正文上的那几块浮层在 doc/DocOverlays.vue；这里只留一个坐标系的壳。
// 这里没有一处 import 取数层：节点树、话题表、图片地址都由上面递进来，动作往上发。
import type { Editor as CoreEditor } from '@tiptap/core'
import type { PluginKey } from '@tiptap/pm/state'
import type { SuggestionProps } from '@tiptap/suggestion'
import type { Block, Topic } from '../../../cx_types'
import type { SlashItem } from '../../../lib/docSlashMenu'

import { computed, nextTick, ref, watch } from 'vue'
import { EditorContent, useEditor } from '@tiptap/vue-3'

import {
  commentMarkKey,
  commentQuoteRanges,
  createCommentMarks,
  createLiveRefBadges,
  createTokenChips,
  liveRefKey,
  mappedCommentQuoteState,
} from '../../../lib/docDecorations'
import { docExtensions, docReplaceRange, serializeDoc } from '../../../lib/docMarkdown'
import { createSlashCommands } from '../../../lib/docSlashMenu'
import LoadingSkeleton from '../../common/LoadingSkeleton.vue'

import { alignedDocBlocks } from './docBlocks'
import DocOverlays from './DocOverlays.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 能不能改。只读时 tiptap 收键盘，浮层里的控件也不出场。 */
    editable: boolean
    /** 正文还在路上：画骨架，编辑器让位。 */
    loading: boolean
    topicId: string | null
    /** 项目话题表：支线徽章的标题与状态、`<#id>` 话题 chip 的标题都从这里查。 */
    topicList?: Topic[]
    /** 段落 index → 支线 id（装饰的原料，取数那一半算好的）。 */
    liveRefIndex?: Map<number, string>
    /** 段落 index → 压在上面的评论（同上）。 */
    commentMarkIndex?: Map<number, { id: string; quote: string }[]>
    openCommentId?: string | null
    /** 取一份最新的节点树：闪某一段、给评论定锚点都要它。 */
    fetchDocNodes: () => Promise<Block[]>
    /** 图片 src 的显示期解析：工作区相对路径走原始文件接口。 */
    imageSrc: (src: string) => string
    /** 段落定位不到时的兜底：整篇闪一下 —— 那一下归页面那一半画。 */
    pulse: () => void
    /** 页面每收到一次正文区的滚动就加一：滚动时收起代码块工具条。 */
    scrollTick?: number
  }>(),
  {
    topicList: () => [],
    liveRefIndex: () => new Map<number, string>(),
    commentMarkIndex: () => new Map<number, { id: string; quote: string }[]>(),
    openCommentId: null,
    scrollTick: 0,
  }
)

// 动作一律往上发：「换了个值」下面自己接住，「做了个动作」交给拿着状态的那一层。
const emit = defineEmits<{
  /** 有人在编辑器里改了东西（装配服务端那一版时不算）。 */
  (e: 'edited'): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  /** 点了正文里的评论下划线：滚到文档底部那张卡。 */
  (e: 'locate-comment', commentId: string): void
  /** 选中一段正文点了「评论」：浮层算好了锚点，转给页面去开写评论的框。 */
  (e: 'open-comment', payload: { anchorId: string | null; quote: string }): void
  /** 当场要说的失败（目前只有复制代码失败）。 */
  (e: 'error', message: string): void
}>()

// Flash a set of editor blocks. We draw transient overlay rectangles positioned
// over the targets rather than styling the blocks — ProseMirror owns and defends
// its editable DOM, so any class we add there is reverted instantly. Overlays live
// in `.doc-editor-wrap` (position: relative) and never touch the editor.
async function flashBlocks(els: HTMLElement[]) {
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  if (els.length === 0 || !wrap) {
    props.pulse()
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
  const aligned = await alignedDocBlocks(() => props.fetchDocNodes())
  await flashBlocks(aligned.filter((a) => a.node.turn_id === turnId).map((a) => a.el))
}

// B4: highlight the single paragraph a comment is anchored to (by doc-node id).
async function highlightNode(nodeId: string) {
  const aligned = await alignedDocBlocks(() => props.fetchDocNodes())
  await flashBlocks(aligned.filter((a) => a.node.id === nodeId).map((a) => a.el))
}

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
    emit('locate-comment', ca.dataset.comment)
    return
  }
  // Links: in READ mode the rendered <a target=_blank> navigates natively; in
  // EDIT mode a plain click places the caret and ⌘/Ctrl-click opens the link
  // (the editor-standard gesture, same as VS Code / Feishu).
  const a = target?.closest('.doc-editor a[href]') as HTMLAnchorElement | null
  if (a && props.editable) {
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

// ---- Slash 菜单的接线。菜单本身（块类型表 + 筛选 + tiptap 扩展）住在
// lib/docSlashMenu.ts，浮层住在 doc/DocSlashMenu.vue；这里只剩「它现在开在哪、
// 停在第几行」，以及 ＋ 手柄那条会自己收尾的路径。
//
// 状态在这里而不在浮层那一半，因为建议插件的回调是建编辑器时一口气接上的 —— 谁建编辑器
// 谁得拿着这几个回调。浮层只负责按 props 把它画出来。
interface SlashMenuState {
  items: SlashItem[]
  index: number
  top: number
  left: number
}
const slashMenu = ref<SlashMenuState | null>(null)
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

function runSlashItem(item: SlashItem) {
  slashProps?.command(item)
}

function onSlashKeyDown({ event }: { event: KeyboardEvent }): boolean {
  const m = slashMenu.value
  if (!m) return false // Escape is handled by the plugin itself (exits)
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    const delta = event.key === 'ArrowDown' ? 1 : -1
    m.index = (m.index + delta + m.items.length) % m.items.length
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

// 浮层里发生的事。鼠标挑一项走的是和键盘同一条 command()；悬停只改选中行。
function onSlashHover(index: number) {
  if (slashMenu.value) slashMenu.value.index = index
}
function onSlashPlanted() {
  plusSlashPending = true
}

// 浮层的鼠标经过接在这个壳上：它量坐标用的就是这个壳的矩形，而鼠标进不了浮层自己的
// 按钮以外的地方（那些按钮会再冒泡回来，各自认领）。
const overlaysRef = ref<InstanceType<typeof DocOverlays> | null>(null)
function onHover(e: MouseEvent) {
  overlaysRef.value?.onHover(e)
}

// Guard: when we programmatically setContent from a server reload we don't want
// onUpdate to flag the doc as dirty.
const loadingFromServer = ref(false)
const displayTick = ref(0)
const commentIndexStale = ref(false)

const editor = useEditor({
  content: '',
  extensions: [
    ...docExtensions({ resolveImageSrc: (src) => props.imageSrc(src) }),
    // 三种装饰都只读递进来的两份输入（哪一段有装饰、装饰上写什么），扩展本身不认识
    // 面板 —— 见 lib/docDecorations.ts。
    createTokenChips({ titleOf: (tid) => props.topicList.find((t) => t.id === tid)?.title }),
    createLiveRefBadges({
      index: () => props.liveRefIndex,
      factsOf: (topicId) => {
        const sub = props.topicList.find((t) => t.id === topicId)
        return { title: sub?.title ?? null, status: sub?.status ?? '' }
      },
    }),
    createCommentMarks({ index: () => props.commentMarkIndex, openId: () => props.openCommentId ?? null }),
    createSlashCommands({
      onStart: showSlashMenu,
      onUpdate: showSlashMenu,
      onExit: onSlashExit,
      onKeyDown: onSlashKeyDown,
    }),
  ],
  editable: props.editable,
  editorProps: {
    attributes: { class: 'doc-prose' },
  },
  onTransaction: ({ transaction }) => {
    displayTick.value++
    if (transaction.docChanged) commentIndexStale.value = true
  },
  onUpdate: () => {
    if (import.meta.env.DEV) {
      const hook = (window as unknown as Record<string, { updates?: number }>).__docPanel
      if (hook) hook.updates = (hook.updates ?? 0) + 1
    }
    // 装配服务端那一版不是人做的编辑。
    if (loadingFromServer.value) return
    // 正文在光标底下换了：那个「评论」按钮指着的段落已经不是原来那一段了。
    overlaysRef.value?.onEdited()
    emit('edited')
  },
})

// 取数那一半把最新的索引递下来时，装饰要立刻照着重建 —— 它算不出 DOM 在哪儿，编辑器
// 在哪儿只有这一层知道。watch 让这一步和索引的更新同一拍发生。
watch(
  () => props.liveRefIndex,
  () => poke(liveRefKey)
)
watch(
  () => props.commentMarkIndex,
  () => poke(commentMarkKey)
)
function poke(key: PluginKey) {
  if (key === commentMarkKey) commentIndexStale.value = false
  const view = editor.value?.view
  if (view) view.dispatch(view.state.tr.setMeta(key, true))
}

watch(
  () => props.openCommentId,
  () => {
    const view = editor.value?.view
    if (view) view.dispatch(view.state.tr.setMeta(commentMarkKey, 'active-only'))
  }
)
function commentQuoteState(id: string): 'unique' | 'missing' | 'ambiguous' {
  void displayTick.value
  const ed = editor.value
  if (!ed) return 'missing'
  const marks =
    commentMarkKey
      .getState(ed.state)
      ?.find()
      .filter((mark: { spec: { commentId?: string } }) => mark.spec.commentId === id) ?? []
  if (marks.length) {
    return mappedCommentQuoteState(ed.state.doc, marks[0])
  }
  if (commentIndexStale.value) return 'missing'
  let status: 'unique' | 'missing' | 'ambiguous' = 'missing'
  ed.state.doc.forEach((node, offset, index) => {
    const comment = props.commentMarkIndex.get(index)?.find((item) => item.id === id)
    if (comment) status = commentQuoteRanges(node, offset, comment.quote).status
  })
  return status
}

// 能不能改这件事两边都要知道：取数那一半拿它判「现在不许自动保存」，这一层拿它判
// tiptap 收不收键盘。emitUpdate=false：tiptap v3 的 setEditable 默认会发一次假 update
// （正文一个字没动），那一下会被当成人在打字，而只读时自动保存又不跑，于是「编辑中…」
// 永远挂在横条上。
watch(
  () => props.editable,
  (v) => editor.value?.setEditable(v, false)
)

// 把服务端的这一版落进编辑器，只替换真正变了的那一段。
//
// 整份 `setContent` 会把所有位置都映射一遍（那一步在语义上先删光再插入），于是停在
// 没变的段落里的光标会被甩到文末。而这篇文档是可以点进去的：点一下只是「我在看这
// 儿」，不会让它变 dirty，所以芝士的下一次更新照常装进来，把人的插入点带走。只重写
// 差异区间就没这回事——区间之前的每一个位置都没被碰过。
//
// 不是为了少重绘：prosemirror-view 本来就逐节点比对、复用没变的 DOM，整份替换也不
// 会把每个段落重建一遍（这一点写过测试，见 lib/docReplaceRange.spec.ts）。
//
// 解析走的是 tiptap 自己那条路：`setContent(md, { contentType: 'markdown' })` 内部
// 也是先 `editor.markdown.parse(md)` 再装 JSON，所以两边解析出来的文档一模一样。
function setEditorMarkdown(md: string) {
  const ed = editor.value
  if (!ed) return
  loadingFromServer.value = true
  try {
    if (!replaceChangedNodes(ed, md)) ed.commands.setContent(md, { contentType: 'markdown' })
  } finally {
    // 整份替换那条兜底路径会抛（解析失败、区间不合法），标志位必须还原，否则之后每一次
    // 真的编辑都不再算 dirty，autosave 就永远不跑了。
    loadingFromServer.value = false
  }
}

/** 返回 false = 这条路走不通（没有 markdown 管理器、解析失败、区间装不进去），
 *  调用方退回整份替换。 */
function replaceChangedNodes(ed: CoreEditor, md: string): boolean {
  const manager = ed.markdown
  if (!manager) return false
  try {
    const next = ed.schema.nodeFromJSON(manager.parse(md))
    const range = docReplaceRange(ed.state.doc, next)
    // null = 两版一模一样。屏幕上已经是它了，一个字都不用动。
    if (!range) return true
    const tr = ed.state.tr.replace(range.from, range.to, next.slice(range.from, range.sliceTo))
    // 服务端刷新不是人做的编辑，不该占一格撤销。
    tr.setMeta('addToHistory', false)
    ed.view.dispatch(tr)
    return true
  } catch {
    return false
  }
}

/** 取数那一半的两个口子之一：把服务端的正文装进编辑器。 */
function installMarkdown(body: string) {
  setEditorMarkdown(body)
}

/** 取数那一半的另一个口子：编辑器里现在这一版正文（markdown）。 */
function serializeVisual(): string | null {
  return editor.value ? serializeDoc(editor.value) : null
}

defineExpose({ editor, installMarkdown, serializeVisual, highlightTurn, highlightNode, commentQuoteState })

// 空文档里的灰字住在 CSS 的 ::before 里；按当前语言取值，带上引号交给 content。
const emptyPlaceholder = computed(() => JSON.stringify(t('work.room.doc.emptyPlaceholder')))
</script>

<template>
  <!-- 根元素上是那两件「针脚」：⌘S 从页面原样落到这里（存不存是取数那一半的事），
       鼠标经过转给浮层量坐标（坐标系就是这个壳的矩形）。 -->
  <div class="doc-editor-wrap" @click="onDocClick" @mouseover="onHover">
    <!-- 正文还在路上时画它的节奏，别把编辑器摆出来：一个空的编辑器会亮出
         「AI 队友会在这里维护文档」那句占位话，而那句话的意思是「这篇文档是空
         的」——文档有内容、只是还没到，说的就是假话。编辑器本身不卸载
         （v-show），卸了它每换一个话题都要重建一次。 -->
    <LoadingSkeleton v-if="loading" variant="doc" class="doc-skel" />
    <EditorContent v-if="editor" v-show="!loading" :editor="editor" class="doc-editor" />
    <!-- 压在正文上的那几块：评论 CTA、slash 菜单、代码块工具条、块手柄。 -->
    <DocOverlays
      ref="overlaysRef"
      :editor="editor"
      :editable="editable"
      :topic-id="topicId"
      :fetch-doc-nodes="fetchDocNodes"
      :slash-menu="slashMenu"
      :scroll-tick="scrollTick"
      @open-comment="emit('open-comment', $event)"
      @error="emit('error', $event)"
      @pick="runSlashItem"
      @hover="onSlashHover"
      @planted="onSlashPlanted"
    />
  </div>
</template>

<style scoped>
.doc-editor-wrap {
  position: relative;
}
/* 骨架站在 .doc-prose 的位置上：同样 720px 封顶、居中。 */
.doc-skel {
  max-width: 720px;
  margin: 0 auto;
}
/* Placeholder as ::before INSIDE the empty first paragraph: the ghost text
   shares the paragraph's exact font metrics, so the caret sits cleanly at
   its left edge instead of cutting through a misaligned overlay. PM renders
   an empty doc as <p><br class="ProseMirror-trailingBreak"></p>. */
.doc-editor :deep(.doc-prose > p:first-child:last-child:has(> br.ProseMirror-trailingBreak:only-child))::before {
  content: v-bind(emptyPlaceholder);
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
/* ProseMirror editable area — reads like a clean document column. */
.doc-editor :deep(.doc-prose) {
  outline: none;
  min-height: 240px;
  max-width: 720px;
  margin: 0 auto;
  font-size: 16px;
  line-height: 1.5;
  overflow-wrap: break-word;
  caret-color: var(--ink);
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

/* Feishu-style comment anchor: a quiet dashed underline; hover fills. */
.doc-editor :deep(.comment-anchor) {
  text-decoration-line: underline;
  text-decoration-style: dotted;
  text-decoration-color: color-mix(in srgb, var(--muted) 40%, transparent);
  text-decoration-thickness: 2px;
  text-underline-offset: 4px;
  cursor: pointer;
}
.doc-editor :deep(.comment-anchor.is-active) {
  background: var(--fill);
  text-decoration-color: var(--accent-ink);
}
.doc-editor :deep(.comment-anchor:hover) {
  background: var(--fill);
}
/* Source-backed document hierarchy, shared by editing and read-only modes. */
.doc-editor :deep(h1),
.doc-editor :deep(h2),
.doc-editor :deep(h3),
.doc-editor :deep(h4),
.doc-editor :deep(h5),
.doc-editor :deep(h6) {
  font-weight: 600;
  line-height: 1.5;
  margin: 12px 0 -4px;
  color: var(--ink);
}
.doc-editor :deep(h1) {
  font-size: 22px;
}
.doc-editor :deep(h2) {
  font-size: 18px;
}
.doc-editor :deep(h3),
.doc-editor :deep(h4) {
  font-size: 16px;
}
.doc-editor :deep(h5),
.doc-editor :deep(h6) {
  font-size: 14px;
}
/* The doc starts flush: no phantom gap above a leading heading. */
.doc-editor :deep(.doc-prose > :first-child) {
  margin-top: 0;
}
.doc-editor :deep(p) {
  margin: 0;
}
.doc-editor :deep(ul),
.doc-editor :deep(ol) {
  margin: 0 0 12px;
  padding-left: 32px;
}
.doc-editor :deep(li) {
  margin: 0;
  padding-inline-start: 8px;
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
  accent-color: var(--ink);
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
  border-left: 4px solid var(--line);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
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
  /* 装饰性角标，不按可读下限走：它蹲在第一行代码的右上角，放大到 12px 就压住
     长行的字（量过：标签到 19px，第一行从 16px 起）。 */
  font-size: 12px;
  letter-spacing: 0.04em;
  color: var(--faint);
  text-transform: lowercase;
  pointer-events: none;
  transition: opacity var(--dur-quick) var(--ease-standard);
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
  text-decoration: underline;
  text-underline-offset: 2px;
  text-decoration-thickness: 1px;
  cursor: pointer;
}
.doc-editor :deep(a:hover) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
/* 图片: soft corners, never wider than the column. */
.doc-editor :deep(.doc-prose > *) {
  min-width: 0;
  margin-bottom: 12px;
}
.doc-editor :deep(.doc-prose ul),
.doc-editor :deep(.doc-prose ol) {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.doc-editor :deep(.doc-prose li ul),
.doc-editor :deep(.doc-prose li ol) {
  margin: 4px 0 0;
}
.doc-editor :deep(.doc-prose li > p) {
  margin: 0;
}
.doc-editor :deep(.doc-prose a) {
  cursor: text;
}
.doc-editor :deep(.doc-prose[contenteditable='false'] a) {
  cursor: pointer;
}
.doc-editor :deep(.doc-prose pre code) {
  white-space: pre;
}
.doc-editor :deep(.doc-prose table) {
  line-height: 1.7;
}
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
  background: var(--fill);
}
</style>
