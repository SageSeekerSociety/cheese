<script setup lang="ts">
// 文档编辑器的壳 —— tiptap 实例、协同文档的接线，和闪哪一段这类对外的动作。
//
// 一层里分了两处住：画正文列的那一半（连它那一整套 `:deep()` 排版）在
// doc/DocProse.vue，压在正文上的浮层在 doc/DocOverlays.vue，slash 菜单的状态在
// composables/useDocSlashMenu.ts。这里只剩壳本身：建编辑器、把它接到递进来的协同文档
// 上、把坐标和动作转出去。壳上那个 `.doc-editor-wrap` 留着，因为浮层量坐标、闪一段的
// 浮块定位，都以它的矩形为准。
//
// 正文不经过 props：编辑器直接绑在递进来的那份协同文档上（`session`），谁改了什么都
// 从那里来、到那里去，这一层不装也不存。换一间房就是换一份文档，编辑器跟着重建。
//
// 这里没有一处引到取数层：节点树、话题表、图片地址都由上面递进来，动作往上发。
import type { PluginKey } from '@tiptap/pm/state'
import type { DocSession } from '../../../composables/useDocCollab'
import type { MentionPoolEntry } from '../../../composables/useRoomMentionPicker'
import type { Block, Topic } from '../../../cx_types'
import type { CommentSpot } from '../../../lib/docCommentSpots'
import type { DocLinkTarget } from '../../../lib/docLinks'
import type { LinkableDocument, RefItem } from '../../../lib/docMentionMenu'
import type { ThreadPlace } from '../../../lib/docThreadTypes'
import type { CanvasData } from '../../../types/canvas'

import { nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import Collaboration from '@tiptap/extension-collaboration'
import CollaborationCaret from '@tiptap/extension-collaboration-caret'

import { scrollBehavior } from '@/utils/motion'

import { useDocRefMenu } from '../../../composables/useDocRefMenu'
import { useDocSlashMenu } from '../../../composables/useDocSlashMenu'
import { BUBBLE_META } from '../../../lib/docBubble'
import { renderCaret } from '../../../lib/docCaret'
import { placeOf, spotAt } from '../../../lib/docCommentSpots'
import {
  commentHighlightKey,
  createCommentHighlights,
  createEmptyLineHint,
  createTitleEcho,
} from '../../../lib/docDecorations'
import { createEditMarks } from '../../../lib/docEditMarks'
import { captureDocLink, safeDocHref } from '../../../lib/docLinks'
import { commentAnchors, docExtensions, serializeDoc } from '../../../lib/docSchema'
import { createSlashCommands } from '../../../lib/docSlashMenu'
import { refsVersion } from '../../../lib/refDirectory'
import { createTokenChips, tokenChipsKey } from '../../../lib/tokenChips'
import LoadingSkeleton from '../../common/LoadingSkeleton.vue'
import MentionMenu from '../../room/MentionMenu.vue'

import { editorBlocks, newStatusAt } from './blocks'
import { alignedDocBlocks } from './docBlocks'
import { DocEditor } from './docEditor'
import DocLinkCallout from './DocLinkCallout.vue'
import DocOverlays from './DocOverlays.vue'
import DocProse from './DocProse.vue'

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
    /** 这份文档 @ 到并通知过的人：他们的 `<@handle>` 画成 @chip，其余的画成名字。 */
    notifiedMentions?: string[]
    /** 打 @ 时列出来的人。 */
    mentionPeople?: MentionPoolEntry[]
    /** 打 # 时能引用的文档（读的人能读的）。 */
    documentList?: LinkableDocument[]
    /** 这一份自己的 id：不引用自己。 */
    documentId?: string | null
    /** 能写评论（归档话题的文档不能）。 */
    canComment?: boolean
    /** 没解决的评论串：它们评的那几个字标出来。 */
    openThreads?: ReadonlySet<string>
    /** 正在看的那一串：标得重一些。 */
    activeThread?: string | null
    /** 取一份最新的节点树：闪某一段要它。 */
    fetchDocNodes: () => Promise<Block[]>
    /** 文档里引用的一张图拿什么画：取回来的字节做成的 object URL（本来就是地址的原样给回）。 */
    imageSrc: (src: string) => Promise<string>
    /** 文档里的画布拿什么读写；不给就只画标题。 */
    canvas?: CanvasData | null
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
    notifiedMentions: () => [],
    mentionPeople: () => [],
    documentList: () => [],
    documentId: null,
    canComment: true,
    openThreads: () => new Set<string>(),
    activeThread: null,
    scrollTick: 0,
    agentHandle: null,
    canvas: null,
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
  /** 选中一段正文点了 AI 队友；带着 `request` 时，开框就替读者说这一句。 */
  (e: 'agent', payload: CommentSpot, request?: string): void
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
  const el = target?.closest('.mention, .name-ref') as HTMLElement | null
  if (!el) return
  if (el.dataset.topic) emit('open-topic', el.dataset.topic)
  else if (el.dataset.handle) emit('mention-click', el.dataset.handle)
  else if (el.dataset.file) emit('open-file', el.dataset.file)
}

// Slash 菜单（打「/」挑块类型）的状态住在 composables/useDocSlashMenu.ts —— 菜单开在哪、
// 列着什么、停在第几行，以及 ＋ 手柄种下的那个「/」没选就走了怎么收尾。这里只把名字接上：
// 下面建编辑器时递给 createSlashCommands，模板里递给浮层。
const {
  menu: slashMenu,
  show: showSlashMenu,
  run: runSlashItem,
  onKeyDown: onSlashKeyDown,
  onExit: onSlashExit,
  hover: onSlashHover,
  markPlanted: onSlashPlanted,
} = useDocSlashMenu()

// 浮层的鼠标经过接在这个壳上：它量坐标用的就是这个壳的矩形，而鼠标进不了浮层自己的
// 按钮以外的地方（那些按钮会再冒泡回来，各自认领）。
const overlaysRef = ref<InstanceType<typeof DocOverlays> | null>(null)
function onHover(e: MouseEvent) {
  overlaysRef.value?.onHover(e)
}

const displayTick = ref(0)

// 打 @ 挑人、打 # 挑话题（composables/useDocRefMenu.ts）。
const refMenu = useDocRefMenu({
  people: () => props.mentionPeople,
  topics: () => props.topicList,
  documents: () => props.documentList,
  self: () => props.documentId,
})

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
      ...editorBlocks(
        docExtensions({ loadImage: (src) => props.imageSrc(src) }),
        () =>
          props.agentHandle
            ? {
                name: props.agentName,
                run: (ed, from, to, request) => emit('agent', spotAt(ed, from, to), request),
              }
            : null,
        props.canvas ? { ...props.canvas, openTask: (id) => emit('open-topic', id) } : null
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
          notified: (handle: string) => props.notifiedMentions.includes(handle),
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
// 名册比正文晚到：到了之后标签上的账号换成名字。通知过谁也是存回之后才知道的。`<#id>` 指着什么也是后到的
// （lib/refDirectory）。
watch(
  () => [props.mentionNames, props.topicList, props.notifiedMentions, refsVersion.value],
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
</script>

<template>
  <!-- 根元素上是那两件「针脚」：⌘S 从页面原样落到这里（存不存是取数那一半的事），
       鼠标经过转给浮层量坐标（坐标系就是这个壳的矩形）。 -->
  <div class="doc-editor-wrap" @click="onDocClick" @mouseover="onHover" @pointerdown.capture="onPointerDown">
    <!-- 正文还在路上时画它的节奏，别把编辑器摆出来：一个空的编辑器会亮出
         占位的那句灰字，而那句话的意思是「这篇文档是空的」——文档有内容、
         只是还没到，说的就是假话。 -->
    <LoadingSkeleton v-if="loading" variant="doc" class="doc-skel" />
    <!-- The prose column (styles and all) lives in DocProse.vue: the editor
         instance is built here, that half only mounts it. -->
    <DocProse :editor="editor" :loading="loading" :key-focus="keyFocus" :placeholder="placeholder" />
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
/* 正文列那一整套排版（`.doc-editor :deep(...)`）跟着正文列搬去了 DocProse.vue：那些
   规则只有和它们画的元素待在同一个 scoped 样式里才对得上号。这里留下的是壳自己的事 ——
   骨架的位置、闪一段的浮块、文件 chip 的图标。 */
</style>
