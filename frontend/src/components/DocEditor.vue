<script setup lang="ts">
// A self-contained living-doc editor: the same tiptap experience as PanelDoc
// (drag handle, StarterKit + tables + task lists + code highlighting, other
// people's carets), but WITHOUT the workspace chrome (comments, live-refs, tool
// drawers, git panels).
//
// It reuses the ONE shared extension list in lib/docSchema, so this editor and
// PanelDoc can never drift apart on schema, and it binds to the same live
// document (`session`, opened by the page): what anyone types here is in the
// room's document as they type it, and the collaboration service stores it.
import type { Node as PMNode } from '@tiptap/pm/model'
import type { DocSession } from '../composables/useDocCollab'

import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import Collaboration from '@tiptap/extension-collaboration'
import CollaborationCaret from '@tiptap/extension-collaboration-caret'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import { Editor, EditorContent } from '@tiptap/vue-3'

import { renderCaret } from '../lib/docCaret'
import { docExtensions } from '../lib/docSchema'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    // The live document (章程 = the project's root topic's); null until open.
    session: DocSession | null
    // The document has not arrived yet.
    loading?: boolean
    // Read-only render vs. editable rich editor.
    editable?: boolean
    // Placeholder shown when the doc is empty.
    placeholder?: string
  }>(),
  {
    loading: false,
    editable: true,
    placeholder: '',
  }
)

// 块手柄（＋ / ⠿）跟着鼠标悬停出现、靠拖动排序，手机上用不了：不画，也不留那条
// 56px 的槽，正文贴着页边排（样式里 --doc-gutter 在手机上是 0）。960 是外壳换成手机
// 形态的那条线（Vuetify 的 md）。
const mdAndUp = useMediaQuery('(min-width: 960px)')

const empty = ref(true)

// ---- Editor: the SHARED extension list, bound to the live document. A new
// document (another project) is a new editor: Collaboration reads its document
// once, when the editor is built. ----
const editor = shallowRef<Editor | undefined>()
watch(
  () => props.session,
  (session) => {
    editor.value?.destroy()
    editor.value = session
      ? new Editor({
          extensions: [
            ...docExtensions(),
            Collaboration.configure({ document: session.doc }),
            CollaborationCaret.configure({ provider: session.provider, user: session.user, render: renderCaret }),
          ],
          editable: props.editable,
          editorProps: {
            attributes: { class: 'doc-prose' },
          },
          onUpdate: ({ editor: ed }) => {
            empty.value = ed.isEmpty
          },
          onCreate: ({ editor: ed }) => {
            empty.value = ed.isEmpty
          },
        })
      : undefined
  },
  { immediate: true }
)

// ---- DragHandle: track the hovered block so ＋ inserts below / ⠿ reorders. ----
const hoverPos = ref<number | null>(null)
const hoverNodeSize = ref<number>(0)
function onNodeChange(data: { node: PMNode | null; pos: number }) {
  hoverPos.value = data.node ? data.pos : null
  hoverNodeSize.value = data.node?.nodeSize ?? 0
}
function addBlockBelow() {
  const ed = editor.value
  if (!ed || hoverPos.value == null) return
  const hovered = ed.state.doc.nodeAt(hoverPos.value)
  const emptyPara = hovered?.type.name === 'paragraph' && hovered.content.size === 0
  if (emptyPara) {
    ed.chain()
      .focus()
      .setTextSelection(hoverPos.value + 1)
      .run()
    return
  }
  const insertAt = hoverPos.value + hoverNodeSize.value
  ed.chain()
    .focus()
    .insertContentAt(insertAt, { type: 'paragraph' })
    .setTextSelection(insertAt + 1)
    .run()
}

// Editable toggle from the parent: setEditable(v, false) so tiptap's synthetic
// 'update' is not mistaken for typing.
watch(
  () => props.editable,
  (v) => editor.value?.setEditable(v, false)
)

onBeforeUnmount(() => {
  editor.value?.destroy()
})
</script>

<template>
  <div class="doc-editor-wrap" :class="{ readonly: !editable }">
    <div v-if="loading" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>
    <div class="doc-editor">
      <EditorContent v-if="editor" :editor="editor" />
      <!-- Empty-doc hint: a soft placeholder over the blank editor. -->
      <div v-if="editor && !loading && empty && placeholder" class="doc-editor__placeholder">
        {{ placeholder }}
      </div>
      <!-- Feishu-style left gutter block handles: ＋ inserts below, ⠿ reorders.
           Edit mode only, exactly like PanelDoc. -->
      <DragHandle
        v-if="editor && editable && mdAndUp"
        :editor="editor"
        :on-node-change="onNodeChange"
        class="doc-handle"
      >
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
        <span class="doc-handle__btn doc-handle__grip" :title="t('work.room.doc.dragToReorder')">
          <v-icon size="15">mdi-drag-vertical</v-icon>
        </span>
      </DragHandle>
    </div>
  </div>
</template>

<style scoped>
/* Other people's carets: a line and their name, coloured per person by
   lib/docCaret.ts. */
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
/* Mirrors PanelDoc's editor styling so 项目文档 reads identically to the
   workspace doc. Kept to the CORE surface (no workspace-only selectors like
   comments / live-refs / mentions). */
.doc-editor-wrap {
  position: relative;
}
.doc-editor {
  position: relative;
  /* Left gutter that hosts the Feishu-style drag handle. The DragHandle plugin
     pins the handle's RIGHT edge to the text's left edge and it extends ~56px
     leftward; without a dedicated gutter it overflows the panel's left boundary
     (like PanelDoc's .doc-page padding, this reserves the room WITHIN the
     editor so the ＋/⠿ handle sits neatly left of the text, never clipped).
     The placeholder is laid over the same box, so it reads the same gutter. */
  --doc-gutter: 56px;
  padding-left: var(--doc-gutter);
}
/* 手机上没有块手柄（模板里 mdAndUp 才画），槽也不留。 */
@media (max-width: 959.98px) {
  .doc-editor {
    --doc-gutter: 0px;
  }
}
.doc-editor__placeholder {
  position: absolute;
  top: 0;
  left: var(--doc-gutter);
  right: 0;
  max-width: 720px;
  margin: 0 auto;
  color: var(--faint);
  font-size: 16px;
  line-height: 1.8;
  pointer-events: none;
}

/* ProseMirror editable area — a clean document column. */
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

/* GFM tables. */
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
  position: relative;
}
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
.doc-editor :deep(.doc-prose tbody tr:hover td) {
  background: color-mix(in srgb, var(--accent) 3%, transparent);
}

/* Feishu-style left gutter block handles. */
.doc-handle {
  display: flex;
  align-items: center;
  gap: 2px;
  padding-right: 14px;
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

/* ---- Document typography: matches PanelDoc. ---- */
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
/* 任务列表 (GFM `- [ ]`). */
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
/* 代码块. */
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
/* lowlight token colors — the --code-* palette from style.css, shared with
   PanelDoc and with CodeEditor's Monaco theme. See the note in
   panels/doc/DocSurface.vue. */
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
.doc-editor :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
  cursor: pointer;
}
.doc-editor :deep(a:hover) {
  text-decoration: underline;
  text-underline-offset: 3px;
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
</style>
