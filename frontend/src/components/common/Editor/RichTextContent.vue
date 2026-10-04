<script setup lang="ts">
// 富文本的正文排版：编辑和只读两边画的是同一份，所以样式只写在这里。
// 版式照实况文档（DocSurface），字号收到表单和卡片里用的 14px。
import type { Editor } from '@tiptap/vue-3'

import { computed } from 'vue'
import { EditorContent } from '@tiptap/vue-3'

const props = defineProps<{ editor: Editor | undefined; placeholder?: string }>()

const emptyPlaceholder = computed(() => JSON.stringify(props.placeholder ?? ''))
</script>

<template>
  <EditorContent :editor="editor" class="rt-content" />
</template>

<style scoped>
.rt-content :deep(.ProseMirror) {
  outline: none;
  overflow-wrap: break-word;
  caret-color: var(--ink);
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14-loose);
}
.rt-content :deep(.ProseMirror > p:first-child:last-child:has(> br.ProseMirror-trailingBreak:only-child))::before {
  content: v-bind(emptyPlaceholder);
  color: var(--faint);
  pointer-events: none;
  float: left;
  height: 0;
}
.rt-content :deep(.ProseMirror > *) {
  min-width: 0;
  margin: 0 0 8px;
}
.rt-content :deep(.ProseMirror > :last-child) {
  margin-bottom: 0;
}
.rt-content :deep(h1),
.rt-content :deep(h2),
.rt-content :deep(h3),
.rt-content :deep(h4),
.rt-content :deep(h5),
.rt-content :deep(h6) {
  margin: 16px 0 4px;
  color: var(--ink);
  font-weight: 600;
}
.rt-content :deep(h1) {
  font-size: 18px;
  line-height: var(--lh-18);
}
.rt-content :deep(h2) {
  font-size: 15px;
  line-height: var(--lh-15);
}
.rt-content :deep(h3),
.rt-content :deep(h4),
.rt-content :deep(h5),
.rt-content :deep(h6) {
  font-size: 14px;
  line-height: var(--lh-14);
}
.rt-content :deep(.ProseMirror > :first-child) {
  margin-top: 0;
}
.rt-content :deep(p) {
  margin: 0;
}
.rt-content :deep(strong) {
  font-weight: 600;
}
.rt-content :deep(ul),
.rt-content :deep(ol) {
  padding-left: 24px;
}
.rt-content :deep(li) {
  padding-inline-start: 4px;
}
.rt-content :deep(li::marker) {
  color: var(--muted);
}
.rt-content :deep(li ul),
.rt-content :deep(li ol) {
  margin: 4px 0 0;
}
.rt-content :deep(ul[data-type='taskList']) {
  list-style: none;
  padding-left: 4px;
}
.rt-content :deep(ul[data-type='taskList'] li) {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 0;
}
.rt-content :deep(ul[data-type='taskList'] li > label) {
  flex: 0 0 auto;
  user-select: none;
}
.rt-content :deep(ul[data-type='taskList'] li > div) {
  flex: 1 1 auto;
  min-width: 0;
}
.rt-content :deep(ul[data-type='taskList'] input[type='checkbox']) {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--ink);
  vertical-align: middle;
}
.rt-content :deep(ul[data-type='taskList'] li[data-checked='true'] > div) {
  color: var(--muted);
}
.rt-content :deep(blockquote) {
  padding: 4px 12px;
  border-left: 4px solid var(--line);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
}
.rt-content :deep(code) {
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 13px;
}
.rt-content :deep(pre) {
  padding: 12px 16px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--canvas);
  overflow-x: auto;
  font-size: 13px;
  line-height: var(--lh-13);
}
.rt-content :deep(pre code) {
  padding: 0;
  background: none;
  white-space: pre;
}
.rt-content :deep(.hljs-comment),
.rt-content :deep(.hljs-quote) {
  color: var(--code-comment);
  font-style: italic;
}
.rt-content :deep(.hljs-keyword),
.rt-content :deep(.hljs-selector-tag),
.rt-content :deep(.hljs-literal),
.rt-content :deep(.hljs-doctag),
.rt-content :deep(.hljs-meta) {
  color: var(--code-keyword);
}
.rt-content :deep(.hljs-string),
.rt-content :deep(.hljs-regexp),
.rt-content :deep(.hljs-addition) {
  color: var(--code-string);
}
.rt-content :deep(.hljs-number),
.rt-content :deep(.hljs-symbol),
.rt-content :deep(.hljs-bullet) {
  color: var(--code-number);
}
.rt-content :deep(.hljs-title),
.rt-content :deep(.hljs-section),
.rt-content :deep(.hljs-name),
.rt-content :deep(.hljs-function) {
  color: var(--code-function);
}
.rt-content :deep(.hljs-type),
.rt-content :deep(.hljs-class),
.rt-content :deep(.hljs-built_in),
.rt-content :deep(.hljs-attr),
.rt-content :deep(.hljs-attribute),
.rt-content :deep(.hljs-variable),
.rt-content :deep(.hljs-template-variable) {
  color: var(--code-type);
}
.rt-content :deep(.hljs-deletion) {
  color: var(--code-deletion);
}
.rt-content :deep(hr) {
  margin: 16px 0;
  border: none;
  border-top: 1px solid var(--line-2);
}
.rt-content :deep(a) {
  color: var(--accent-ink);
  text-decoration: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
}
.rt-content :deep(.tableWrapper) {
  overflow-x: auto;
}
.rt-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  line-height: var(--lh-13);
}
.rt-content :deep(th),
.rt-content :deep(td) {
  position: relative;
  padding: 4px 8px;
  border: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
.rt-content :deep(th) {
  background: var(--canvas);
  font-weight: 600;
}
.rt-content :deep(.selectedCell::after) {
  content: '';
  position: absolute;
  inset: 0;
  z-index: var(--z-raised-2);
  pointer-events: none;
  background: var(--fill);
}
.rt-content :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-md);
}
</style>
