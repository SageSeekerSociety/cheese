<script setup lang="ts">
// 正文列本身 —— tiptap 把这一份协同文档画成的一段正文，和它上面那一整套排版。
//
// 为什么单独一个组件：正文的样式里全是 `:deep()`，而 `:deep()` 只有 .vue 的
// `<style scoped>` 才被 stylelint 认（放进独立 .css 会红）——所以画正文的那一半
// 必须是一个 .vue，那就连同它画的那个元素一起住在这儿。
//
// 编辑器实例不在这儿建：它是上面那一层拿着的（`EditorContent` 只是把已有实例挂
// 出来）。这里按 props 画：实例、正文在不在路上、键盘焦点环、整篇空时那一句灰字。
import type { Editor } from '@tiptap/vue-3'

import { computed } from 'vue'
import { EditorContent } from '@tiptap/vue-3'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 这一篇的编辑器；还没建好时没有。 */
    editor?: Editor | null
    /** 正文还在路上：编辑器让位，别亮出「这篇文档是空的」那句假话。 */
    loading: boolean
    /** 焦点由键盘带进正文时，给编辑器盒子画一圈。 */
    keyFocus?: boolean
    /** 整篇是空的时候那一行灰字；不给就用话题文档的那一句。 */
    placeholder?: string
  }>(),
  {
    editor: null,
    keyFocus: false,
    placeholder: '',
  }
)

// 空文档里的灰字住在 CSS 的 ::before 里；按当前语言取值，带上引号交给 content。
const emptyPlaceholder = computed(() => JSON.stringify(props.placeholder || t('work.room.doc.emptyPlaceholder')))
const emptyLineHint = computed(() => JSON.stringify(t('work.room.doc.emptyLineHint')))
</script>

<template>
  <EditorContent
    v-if="editor"
    v-show="!loading"
    :editor="editor"
    class="doc-editor"
    :class="{ 'doc-editor--keyfocus': keyFocus }"
  />
</template>

<style scoped>
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
/* 整块的建议：记号包在整块外面。要加的块、要删的块和改了类型的块，左边各有一道
   竖线；字照上面的样子绿底或删除线。 */
.doc-editor
  :deep(.doc-suggestion:has(> :is(p, h1, h2, h3, h4, h5, h6, ul, ol, li, blockquote, pre, div, table, details, img))) {
  display: block;
  padding-left: 8px;
  border-bottom: none;
  border-left: 2px solid var(--ok);
}
.doc-editor :deep(del.doc-suggestion:has(> *)) {
  border-left-color: var(--danger);
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
   （keyFocus，见上面那一层）。 */
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
