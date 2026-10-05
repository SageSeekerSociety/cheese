<script setup lang="ts">
// 文档正文的编辑器本身 —— tiptap 实例、几种装饰，和别人的光标。
//
// 为什么单独一个组件：正文的样式里全是 `:deep()`，而 `:deep()` 只有 .vue 的
// `<style scoped>` 才被 stylelint 认（放进独立 .css 会红）——所以画正文的那一半必须是
// 一个 .vue。它同时是唯一拿着编辑器的地方。
//
// 正文不经过 props：编辑器直接绑在递进来的那份协同文档上（`session`），谁改了什么都
// 从那里来、到那里去，这一层不装也不存。换一间房就是换一份文档，编辑器跟着重建。
//
// 压在正文上的那几块浮层在 doc/DocOverlays.vue；这里只留一个坐标系的壳。
// 这里没有一处 import 取数层：节点树、话题表、图片地址都由上面递进来，动作往上发。
import type { PluginKey } from '@tiptap/pm/state'
import type { SuggestionProps } from '@tiptap/suggestion'
import type { DocSession } from '../../../composables/useDocCollab'
import type { MentionPoolEntry } from '../../../composables/useRoomMentionPicker'
import type { Block, Topic } from '../../../cx_types'
import type { CommentSpot } from '../../../lib/docCommentSpots'
import type { DocLinkTarget } from '../../../lib/docLinks'
import type { RefItem } from '../../../lib/docMentionMenu'
import type { SlashItem } from '../../../lib/docSlashMenu'
import type { ThreadPlace } from '../../../lib/docThreadTypes'

import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import Collaboration from '@tiptap/extension-collaboration'
import CollaborationCaret from '@tiptap/extension-collaboration-caret'
import { EditorContent } from '@tiptap/vue-3'

import { scrollBehavior } from '@/utils/motion'

import { caretMenuPos, useDocRefMenu } from '../../../composables/useDocRefMenu'
import { BUBBLE_META } from '../../../lib/docBubble'
import { renderCaret } from '../../../lib/docCaret'
import { placeOf, spotAt } from '../../../lib/docCommentSpots'
import {
  commentHighlightKey,
  createCommentHighlights,
  createEmptyLineHint,
  createTitleEcho,
  createTokenChips,
  tokenChipsKey,
} from '../../../lib/docDecorations'
import { createEditMarks } from '../../../lib/docEditMarks'
import { captureDocLink, safeDocHref } from '../../../lib/docLinks'
import { commentAnchors, docExtensions, serializeDoc } from '../../../lib/docSchema'
import { createSlashCommands } from '../../../lib/docSlashMenu'
import LoadingSkeleton from '../../common/LoadingSkeleton.vue'
import MentionMenu from '../../room/MentionMenu.vue'

import { editorBlocks, newStatusAt } from './blocks'
import { alignedDocBlocks } from './docBlocks'
import { DocEditor } from './docEditor'
import DocLinkCallout from './DocLinkCallout.vue'
import DocOverlays from './DocOverlays.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 能不能改。只读时 tiptap 收键盘，浮层里的控件也不出场。 */
    editable: boolean
    /** 正文还在路上：画骨架，编辑器让位。 */
    loading: boolean
    /** 这一篇的协同文档；还没打开时是 null。 */
    session: DocSession | null
    topicId: string | null
    /** 话题标题：文档第一行若是同样的一级标题就不再画一遍。 */
    title?: string
    /** 项目话题表：`<#id>` 话题 chip 的标题从这里查。 */
    topicList?: Topic[]
    /** handle → 名字：`<@handle>` chip 上写的字。 */
    mentionNames?: Record<string, string>
    /** 打 @ 时列出来的人。 */
    mentionPeople?: MentionPoolEntry[]
    /** 能写评论（归档话题的文档不能）。 */
    canComment?: boolean
    /** 没解决的评论串：它们评的那几个字标出来。 */
    openThreads?: ReadonlySet<string>
    /** 正在看的那一串：标得重一些。 */
    activeThread?: string | null
    /** 取一份最新的节点树：闪某一段要它。 */
    fetchDocNodes: () => Promise<Block[]>
    /** 图片 src 的显示期解析：工作区相对路径走原始文件接口。 */
    imageSrc: (src: string) => string
    /** 段落定位不到时的兜底：整篇闪一下 —— 那一下归页面那一半画。 */
    pulse: () => void
    /** 页面每收到一次正文区的滚动就加一：滚动时收起代码块工具条。 */
    scrollTick?: number
    /** 整篇是空的时候那一行灰字；不给就用话题文档的那一句。 */
    placeholder?: string
    /** 项目 AI 队友的名字和 handle（选中浮条上用）。 */
    agentName: string
    agentHandle?: string | null
  }>(),
  {
    topicList: () => [],
    mentionNames: () => ({}),
    mentionPeople: () => [],
    canComment: true,
    openThreads: () => new Set<string>(),
    activeThread: null,
    scrollTick: 0,
    agentHandle: null,
    title: '',
    placeholder: '',
  }
)

// 动作一律往上发：「换了个值」下面自己接住，「做了个动作」交给拿着状态的那一层。
const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  /** 点了正文里标着的评论：评论栏翻到那一串。 */
  (e: 'locate-comment', threadId: string): void
  /** 选中一段正文点了「评论」：转给页面去开写评论的框。 */
  (e: 'open-comment', payload: CommentSpot): void
  /** 选中一段正文点了 AI 队友。 */
  (e: 'agent', payload: CommentSpot): void
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
  els[0].scrollIntoView({ behavior: scrollBehavior(), block: 'center' })
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

/** 正文滚到这一串评的那几个字；字已经不在了，就滚到原来那个位置，把那一段闪一下。 */
function revealThread(threadId: string): boolean {
  const ed = editor.value
  if (!ed) return false
  const range = commentAnchors(ed.state.doc).get(threadId)?.[0]
  const at = range?.from ?? placeOf(ed, threadId)
  if (at === null || at === undefined) return false
  const { node } = ed.view.domAtPos(at)
  const el = node instanceof Element ? node : node.parentElement
  if (range) {
    el?.scrollIntoView({ behavior: scrollBehavior(), block: 'center' })
    return true
  }
  const block = el?.closest('.ProseMirror > *') as HTMLElement | null
  if (block) void flashBlocks([block])
  return !!block
}

function threadPlace(threadId: string): ThreadPlace {
  void displayTick.value
  const ed = editor.value
  if (!ed) return null
  if (commentAnchors(ed.state.doc).has(threadId)) return 'marked'
  return placeOf(ed, threadId) === null ? null : 'placed'
}

const linkTarget = shallowRef<DocLinkTarget | null>(null)
function openLink(target: DocLinkTarget) {
  if (target.editor.isDestroyed || target.editor.state.doc !== target.doc) return
  linkTarget.value = target
}
watch(
  () => [props.topicId, props.editable],
  () => {
    linkTarget.value = null
  }
)
watch(
  () => props.scrollTick,
  () => {
    if (linkTarget.value) openLink(linkTarget.value)
  }
)

// Chip clicks in the doc (delegated — decorations are plain spans).
function onDocClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  // A commented passage → its thread in the comment panel.
  const ca = target?.closest('.doc-comment-mark') as HTMLElement | null
  if (ca?.dataset.comment) {
    emit('locate-comment', ca.dataset.comment)
    return
  }
  // Links: in READ mode the rendered <a target=_blank> navigates natively; in
  // EDIT mode a plain click places the caret and ⌘/Ctrl-click opens the link
  // (the editor-standard gesture, same as VS Code / Feishu).
  const a = target?.closest('.doc-editor a[href]') as HTMLAnchorElement | null
  if (a && props.editable && editor.value) {
    e.preventDefault()
    if (e.metaKey || e.ctrlKey || e.shiftKey) {
      if (safeDocHref(a.getAttribute('href') ?? '')) window.open(a.href, '_blank', 'noopener')
    } else {
      const position = editor.value.view.posAtDOM(a, 0)
      const link = captureDocLink(editor.value, position)
      if (link) openLink(link)
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

function showSlashMenu(p: SuggestionProps<SlashItem, SlashItem>) {
  slashProps = p
  const pos = caretMenuPos(p.clientRect, Math.min(p.items.length, 8) * 33 + 10)
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

const displayTick = ref(0)

// 打 @ 挑人、打 # 挑话题（composables/useDocRefMenu.ts）。
const refMenu = useDocRefMenu({ people: () => props.mentionPeople, topics: () => props.topicList })

// 编辑器绑在这一份协同文档上：文档换了（换房间、重连拿到的是另一份）就整个重建，
// 因为 Collaboration 扩展只在建编辑器时认一次文档。
const editor = shallowRef<DocEditor | undefined>()

// 焦点环只给键盘：焦点被键盘带进正文时，编辑器盒子画一圈；鼠标点进来不画，光标本身
// 就是落点。可编辑区对 `:focus-visible` 不分鼠标键盘（一点就亮），所以自己记：焦点进来
// 之前按没按过鼠标。
const keyFocus = ref(false)
let pointing = false
function onPointerDown() {
  pointing = true
  keyFocus.value = false
}

function buildEditor(session: DocSession): DocEditor {
  return new DocEditor({
    extensions: [
      ...editorBlocks(docExtensions({ resolveImageSrc: (src) => props.imageSrc(src) }), () =>
        props.agentHandle ? { name: props.agentName, run: (ed, from, to) => emit('agent', spotAt(ed, from, to)) } : null
      ),
      Collaboration.configure({ document: session.doc }),
      CollaborationCaret.configure({ provider: session.provider, user: session.user, render: renderCaret }),
      // 几种装饰都只读递进来的输入（哪一段有装饰、装饰上写什么），扩展本身不认识
      // 面板 —— 见 lib/docDecorations.ts。
      createTitleEcho({ title: () => props.title }),
      createTokenChips({
        names: () => ({
          mentionNames: props.mentionNames,
          topicTitles: Object.fromEntries(props.topicList.map((t) => [t.id, t.title])),
        }),
      }),
      createCommentHighlights({ open: () => props.openThreads, active: () => props.activeThread ?? null }),
      createEditMarks(),
      createEmptyLineHint(),
      createSlashCommands({
        onStart: showSlashMenu,
        onUpdate: showSlashMenu,
        onExit: onSlashExit,
        onKeyDown: onSlashKeyDown,
        onAction: (_action, ed) => newStatusAt(ed),
      }),
      refMenu.extension,
    ],
    editable: props.editable,
    editorProps: {
      attributes: { class: 'doc-prose' },
    },
    onTransaction: () => {
      displayTick.value++
    },
    onFocus: () => {
      keyFocus.value = !pointing
      pointing = false
    },
    onBlur: () => {
      keyFocus.value = false
      pointing = false
    },
    onUpdate: ({ transaction }) => {
      // 正文在光标底下换了：浮条指着的段落已经不是原来那一段了。浮条自己改的格式除外。
      if (!transaction.getMeta(BUBBLE_META)) overlaysRef.value?.onEdited()
    },
  })
}

watch(
  () => props.session,
  (session) => {
    editor.value?.destroy()
    editor.value = session ? buildEditor(session) : undefined
    linkTarget.value = null
  },
  { immediate: true }
)
onBeforeUnmount(() => editor.value?.destroy())

watch(
  () => [props.openThreads, props.activeThread],
  () => poke(commentHighlightKey)
)
// 名册比正文晚到：到了之后标签上的账号换成名字。
watch(
  () => [props.mentionNames, props.topicList],
  () => poke(tokenChipsKey)
)
function poke(key: PluginKey) {
  const view = editor.value?.view
  if (view) view.dispatch(view.state.tr.setMeta(key, true))
}

// 能不能改：tiptap 收不收键盘。emitUpdate=false：tiptap v3 的 setEditable 默认会发一次
// 假 update（正文一个字没动），评论按钮会把它当成正文换了。
watch(
  () => props.editable,
  (v) => editor.value?.setEditable(v, false)
)

/** 编辑器里现在这一版正文（markdown）。 */
function serializeVisual(): string | null {
  return editor.value ? serializeDoc(editor.value) : null
}

defineExpose({
  editor,
  serializeVisual,
  highlightTurn,
  revealThread,
  threadPlace,
})

// 空文档里的灰字住在 CSS 的 ::before 里；按当前语言取值，带上引号交给 content。
const emptyPlaceholder = computed(() => JSON.stringify(props.placeholder || t('work.room.doc.emptyPlaceholder')))
const emptyLineHint = computed(() => JSON.stringify(t('work.room.doc.emptyLineHint')))
</script>

<template>
  <!-- 根元素上是那两件「针脚」：⌘S 从页面原样落到这里（存不存是取数那一半的事），
       鼠标经过转给浮层量坐标（坐标系就是这个壳的矩形）。 -->
  <div class="doc-editor-wrap" @click="onDocClick" @mouseover="onHover" @pointerdown.capture="onPointerDown">
    <!-- 正文还在路上时画它的节奏，别把编辑器摆出来：一个空的编辑器会亮出
         占位的那句灰字，而那句话的意思是「这篇文档是空的」——文档有内容、
         只是还没到，说的就是假话。编辑器本身不卸载
         （v-show），卸了它每换一个话题都要重建一次。 -->
    <LoadingSkeleton v-if="loading" variant="doc" class="doc-skel" />
    <EditorContent
      v-if="editor"
      v-show="!loading"
      :editor="editor"
      class="doc-editor"
      :class="{ 'doc-editor--keyfocus': keyFocus }"
    />
    <Transition name="doc-menu">
      <DocLinkCallout v-if="linkTarget" :target="linkTarget" @close="linkTarget = null" />
    </Transition>
    <MentionMenu
      :open="!!refMenu.menu.value"
      :matches="refMenu.menu.value?.items ?? []"
      :active-index="refMenu.menu.value?.index ?? 0"
      level="root"
      :enter-sends="true"
      :at="refMenu.menu.value"
      @pick="refMenu.pick($event as RefItem)"
      @hover="refMenu.hover"
    />
    <!-- 压在正文上的那几块：评论 CTA、slash 菜单、代码块工具条、块手柄。 -->
    <DocOverlays
      ref="overlaysRef"
      :editor="editor"
      :editable="editable"
      :topic-id="topicId"
      :slash-menu="slashMenu"
      :scroll-tick="scrollTick"
      :agent-name="agentName"
      :agent-handle="agentHandle"
      :can-agent="!!agentHandle"
      :can-comment="canComment"
      @open-comment="emit('open-comment', $event)"
      @agent="emit('agent', $event)"
      @open-link="openLink"
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
/* 光标所在的空段落里那一行淡字（lib/docDecorations.ts 的 createEmptyLineHint）。 */
.doc-editor :deep(.doc-empty-line)::before {
  content: v-bind(emptyLineHint);
  color: var(--faint);
  pointer-events: none;
  float: left;
  height: 0;
}
/* 与话题标题一模一样的第一行大标题：面板上方已经有了，不画第二遍。 */
.doc-editor :deep(.doc-title-echo) {
  display: none;
}
/* 别人的光标：一条竖线和名字。颜色是那个人的，由 lib/docCaret.ts 写在元素上。 */
.doc-editor :deep(.collaboration-carets__caret) {
  position: relative;
  margin-left: -1px;
  margin-right: -1px;
  border-left: 1px solid;
  border-right: 1px solid;
  word-break: normal;
  pointer-events: none;
}
.doc-editor :deep(.collaboration-carets__label) {
  position: absolute;
  top: -1.4em;
  left: -1px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
  white-space: nowrap;
  user-select: none;
}
/* 修改建议（lib/docSchema/suggestions.ts）：要加的字绿底，要删的字红色删除线。 */
.doc-editor :deep(ins.doc-suggestion) {
  border-bottom: 1px solid var(--ok);
  background: var(--ok-wash);
  color: var(--ok-ink);
  text-decoration: none;
  cursor: pointer;
}
.doc-editor :deep(del.doc-suggestion) {
  color: var(--danger-ink);
  text-decoration: line-through var(--danger);
  cursor: pointer;
}
.doc-editor :deep(.doc-suggestion-focus) {
  outline: 2px solid color-mix(in srgb, var(--ok) 45%, transparent);
  outline-offset: 1px;
}
/* 「查看改动」：改后的字淡绿底，原来的字删除线在它前面（lib/docEditMarks.ts）。 */
.doc-editor :deep(.doc-review-new) {
  background: var(--ok-wash);
  cursor: pointer;
  animation: docReviewIn var(--dur-slow) var(--ease-out);
}
.doc-editor :deep(.doc-review-old) {
  margin-right: 2px;
  color: var(--danger-ink);
  text-decoration: line-through var(--danger);
  user-select: none;
  animation: docReviewIn var(--dur-slow) var(--ease-out);
}
@keyframes docReviewIn {
  from {
    background: transparent;
    opacity: 0.4;
  }
}
.doc-editor :deep(.doc-review-new.is-active),
.doc-editor :deep(.doc-review-old.is-active) {
  outline: 2px solid color-mix(in srgb, var(--ok) 45%, transparent);
  outline-offset: 1px;
}
/* 让 AI 队友改的那一段（lib/docEditMarks.ts）：选中、改写中（带一个写着名字的光标）、
   刚改好时亮一下。 */
.doc-editor :deep(.doc-edit-target--select),
.doc-editor :deep(.doc-edit-target--pending) {
  background: var(--selection-bg);
}
/* 在改：选中的那段一明一暗，看得出它正在被处理。 */
.doc-editor :deep(.doc-edit-target--pending) {
  animation: docEditPending 1.4s ease-in-out infinite;
}
@keyframes docEditPending {
  50% {
    background: color-mix(in srgb, var(--selection-bg) 45%, transparent);
  }
}
@media (prefers-reduced-motion: reduce) {
  .doc-editor :deep(.doc-review-new),
  .doc-editor :deep(.doc-review-old),
  .doc-editor :deep(.doc-edit-target--pending) {
    animation: none;
  }
}
.doc-editor :deep(.doc-edit-target--flash) {
  animation: docEditFlash 2.4s var(--ease-out) forwards;
}
@keyframes docEditFlash {
  0%,
  60% {
    background: var(--ok-wash);
  }
  100% {
    background: transparent;
  }
}
.doc-editor :deep(.doc-edit-caret) {
  position: relative;
  margin-right: -2px;
  border-right: 2px solid var(--inverse-surface);
  pointer-events: none;
}
.doc-editor :deep(.doc-edit-caret__label) {
  position: absolute;
  bottom: 100%;
  left: 0;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  border-bottom-left-radius: 0;
  background: var(--inverse-surface);
  color: var(--inverse-ink);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
  user-select: none;
}
/* B1 Phase 2: flash the exact paragraph(s) a turn produced. Rendered as an
   overlay (not a class on the paragraph) because ProseMirror reverts foreign
   mutations to its editable DOM. `:deep` because these divs are created
   imperatively inside the (scoped) .doc-editor-wrap. */
.doc-editor-wrap :deep(.node-flash-overlay) {
  position: absolute;
  z-index: var(--z-raised-3);
  pointer-events: none;
  border-radius: var(--radius-sm);
  margin: -3px -8px;
  padding: 3px 8px;
  box-sizing: content-box;
  /* 「这几段是它写的」：和改动同一种绿，不拿琥珀装饰（设计系统 §1.6）。 */
  background: var(--ok-wash);
  animation: nodeFlash 1.5s var(--ease-out) forwards;
}
@keyframes nodeFlash {
  0%,
  40% {
    background: var(--ok-wash);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--ok) 45%, transparent);
  }
  100% {
    background: transparent;
    box-shadow: 0 0 0 1px transparent;
  }
}
/* 不动的时候它还得在：底色留着，到时由脚本拿走（flashBlocks）。 */
@media (prefers-reduced-motion: reduce) {
  .doc-editor-wrap :deep(.node-flash-overlay) {
    animation: none;
  }
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
  /* 最后一块下面留一条能点的空白：点在这里，光标落到最后一块后面（见 blocks/blockEditing.ts）。 */
  padding-bottom: 32px;
  max-width: 720px;
  margin: 0 auto;
  /* 长文的阅读档，和文档站正文同一档（设计系统 §3.2）：一篇文档一读就是几屏，
     聊天那一档 15 / 24 放在这里字偏小、行偏挤。 */
  font-size: 16px;
  line-height: 28px;
  overflow-wrap: break-word;
  caret-color: var(--ink);
  color: var(--text);
}
/* 正文自己不画 outline；焦点环画在外面的编辑器盒子上，只在焦点由键盘带进来时
   （keyFocus，见上面的脚本）。 */
.doc-editor :deep(.doc-prose:focus) {
  outline: none;
}
.doc-editor--keyfocus {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* Tables look as styles/docBlocks.css draws them; the cell is the anchor for
   the .selectedCell::after overlay below. */
.doc-editor :deep(.doc-prose :is(th, td)) {
  position: relative;
}
/* CellSelection feedback: prosemirror-tables marks selected cells with
   .selectedCell but ships no styling — without this, dragging across cells
   looked like the selection was lost (it wasn't). */
.doc-editor :deep(.doc-prose .selectedCell::after) {
  content: '';
  position: absolute;
  inset: 0;
  z-index: var(--z-raised-2);
  pointer-events: none;
  background: var(--selection-bg);
}

/* 有人评论的那几个字：浅琥珀底、下面一道琥珀线；正在看的那一串更重一些。 */
.doc-editor :deep(.doc-comment-mark) {
  background: color-mix(in srgb, var(--accent-wash) 70%, transparent);
  border-bottom: 2px solid color-mix(in srgb, var(--accent) 55%, transparent);
  cursor: pointer;
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-editor :deep(.doc-comment-mark:hover),
.doc-editor :deep(.doc-comment-mark.is-active) {
  background: var(--accent-wash);
  border-bottom-color: var(--accent);
}
/* Source-backed document hierarchy, shared by editing and read-only modes. */
.doc-editor :deep(h1),
.doc-editor :deep(h2),
.doc-editor :deep(h3),
.doc-editor :deep(h4),
.doc-editor :deep(h5),
.doc-editor :deep(h6) {
  font-weight: 600;
  color: var(--ink);
}
/* 标题按设计系统的字号表（23 / 18），再往下与正文同大、只靠字重分级。上面空得比下面
   多：标题跟着它下面那一段走。 */
.doc-editor :deep(h1) {
  margin: 32px 0 8px;
  font-size: 23px;
  line-height: var(--lh-23);
}
.doc-editor :deep(h2) {
  margin: 24px 0 8px;
  font-size: 18px;
  line-height: var(--lh-18);
}
.doc-editor :deep(h3),
.doc-editor :deep(h4) {
  margin: 16px 0 4px;
  font-size: 16px;
  line-height: 28px;
}
.doc-editor :deep(h5),
.doc-editor :deep(h6) {
  margin: 16px 0 4px;
  font-size: 15px;
  line-height: var(--lh-15);
}
/* The doc starts flush: no phantom gap above a leading heading. */
.doc-editor :deep(.doc-prose > :first-child) {
  margin-top: 0;
}
.doc-editor :deep(p) {
  margin: 0;
}
/* 连着的两段之间留一点：回车分开的就是两段话，挤在一起读着像一段。 */
.doc-editor :deep(p + p) {
  margin-top: 8px;
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
.doc-editor :deep(mark) {
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--signal-yellow) 32%, transparent);
  color: inherit;
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
  margin: 8px 0;
  background: transparent;
}
.doc-editor :deep(blockquote p:last-child) {
  margin-bottom: 0;
}
.doc-editor :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0 4px;
  border-radius: var(--radius-sm);
  font-size: 14px;
}
/* 代码块: light ground + hairline, language tag in the top-right corner
   (hidden while hovered — the copy button takes that spot). */
.doc-editor :deep(pre) {
  position: relative;
  background: var(--canvas);
  border: 1px solid var(--line);
  padding: 12px 16px;
  border-radius: var(--radius-md);
  overflow-x: auto;
  margin: 16px 0;
  font-size: 14px;
  line-height: var(--lh-14-loose);
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
.doc-editor :deep(hr) {
  border: none;
  border-top: 1px solid var(--line-2);
  margin: 24px 0;
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
.doc-editor :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-md);
  display: block;
  margin: 12px 0;
}
/* 整块被选中（点了图表或图片，或在图表、表格这类块后面按一下退格）：框出来，再按
   退格删掉的就是它。 */
.doc-editor :deep(img.ProseMirror-selectednode),
.doc-editor :deep(.doc-prose > .ProseMirror-selectednode) {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
/* Table rows breathe on hover (body only, not the header). */
.doc-editor :deep(.doc-prose tbody tr:hover td) {
  background: var(--fill);
}
</style>
