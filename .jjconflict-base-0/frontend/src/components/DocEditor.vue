<script setup lang="ts">
// A self-contained living-doc editor: the same tiptap experience as DocPanel
// (drag handle, StarterKit + tables + task lists + code highlighting), but
// WITHOUT the workspace chrome (comments, live-refs, tool drawers, git panels).
//
// It reuses the ONE shared extension list from docMarkdown.ts — so this editor
// and DocPanel can never drift apart on schema/round-trip fidelity — and the
// same getDoc/putDoc API + autosave contract, so 项目文档 edits persist exactly
// like the workspace doc does.
import { myHandle } from '../me'
import { onBeforeUnmount, ref, watch } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import { DragHandle } from '@tiptap/extension-drag-handle-vue-3'
import type { Node as PMNode } from '@tiptap/pm/model'
import { compareRoundTrip, docExtensions, serializeDoc } from '../lib/docMarkdown'
import { getDoc, putDoc } from '../api'

const props = withDefaults(
  defineProps<{
    // The topic whose living doc we edit (章程 = the project's root topic).
    topicId: string | null
    // Read-only render vs. editable rich editor.
    editable?: boolean
    // Placeholder shown when the doc is empty.
    placeholder?: string
  }>(),
  {
    editable: true,
    placeholder: '',
  },
)

// State surfaced to the parent so it can show 保存中… / 已保存 / 未保存.
const emit = defineEmits<{
  (e: 'saving'): void
  (e: 'saved'): void
  (e: 'dirty'): void
  (e: 'error', message: string): void
}>()

const AUTHOR = myHandle()

const loading = ref(false)
const saving = ref(false)
const dirty = ref(false)
// True while WE are installing server content — suppresses the onUpdate dirty
// flip that tiptap's setContent would otherwise trigger.
const loadingFromServer = ref(false)

// The raw doc exactly as stored on the server (git-tracked markdown file).
const rawDoc = ref<string>('')
// 军规 1: a lossy load (parse→serialize differs from disk) pauses autosave so a
// visual edit can't silently rewrite unsupported syntax.
const lossy = ref(false)

// ---- Editor: the SHARED extension list, plus the DragHandle in the template. ----
const editor = useEditor({
  content: '',
  extensions: [...docExtensions()],
  editable: props.editable,
  editorProps: {
    attributes: { class: 'doc-prose' },
  },
  onUpdate: () => {
    if (loadingFromServer.value) return
    dirty.value = true
    emit('dirty')
    queueAutosave()
  },
})

function currentMarkdown(): string {
  const ed = editor.value
  if (!ed) return rawDoc.value
  return serializeDoc(ed)
}

function setEditorMarkdown(md: string) {
  const ed = editor.value
  if (!ed) return
  loadingFromServer.value = true
  ed.commands.setContent(md, { contentType: 'markdown' })
  loadingFromServer.value = false
}

function installDoc(full: string) {
  rawDoc.value = full
  setEditorMarkdown(full)
  const ed = editor.value
  if (ed) {
    const report = compareRoundTrip(full, serializeDoc(ed))
    lossy.value = !report.clean
  }
}

async function loadDoc(topicId: string) {
  loading.value = true
  try {
    const block = await getDoc(topicId)
    if (props.topicId !== topicId) return // guard against fast switches
    installDoc(block?.content ?? '')
    dirty.value = false
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '加载文档失败')
  } finally {
    if (props.topicId === topicId) loading.value = false
  }
}

// Feishu-style autosave, debounced from the last keystroke. 军规 1: a lossy doc
// pauses visual autosave — writing the round-tripped doc back would destroy the
// unsupported syntax.
let autosaveTimer: ReturnType<typeof setTimeout> | null = null
function queueAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(() => {
    if (lossy.value) return
    if (dirty.value && props.editable && !saving.value) void save()
  }, 2500)
}

async function save() {
  const topicId = props.topicId
  if (!topicId || saving.value) return
  const full = currentMarkdown()
  if (full === rawDoc.value) {
    dirty.value = false
    return
  }
  saving.value = true
  emit('saving')
  try {
    await putDoc(topicId, full, AUTHOR)
    rawDoc.value = full
    // Lost-update guard: an edit that landed while the save was in flight must
    // not have its dirty flag wiped by this completion.
    if (currentMarkdown() === full) {
      dirty.value = false
      emit('saved')
    } else {
      dirty.value = true
      queueAutosave()
    }
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function onBlur() {
  if (dirty.value && !lossy.value) void save()
}

function onKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    if (dirty.value && props.editable) void save()
  }
}

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
    ed.chain().focus().setTextSelection(hoverPos.value + 1).run()
    return
  }
  const insertAt = hoverPos.value + hoverNodeSize.value
  ed.chain()
    .focus()
    .insertContentAt(insertAt, { type: 'paragraph' })
    .setTextSelection(insertAt + 1)
    .run()
}

// Topic switch: full reload.
watch(
  () => props.topicId,
  (id) => {
    if (id) void loadDoc(id)
    else {
      rawDoc.value = ''
      dirty.value = false
      setEditorMarkdown('')
    }
  },
  { immediate: true },
)

// Editable toggle from the parent: setEditable(v, false) so tiptap's synthetic
// 'update' doesn't mark the doc dirty. Leaving edit mode flushes unsaved edits.
watch(
  () => props.editable,
  (v) => {
    editor.value?.setEditable(v, false)
    if (!v && dirty.value) void save()
  },
)

// Reload from the server (e.g. AI activity). Respects unsaved local edits.
async function reload() {
  const id = props.topicId
  if (!id || dirty.value) return
  try {
    const block = await getDoc(id)
    if (props.topicId !== id) return
    const full = block?.content ?? ''
    if (full !== rawDoc.value) installDoc(full)
  } catch {
    // best-effort
  }
}

defineExpose({ save, reload })

onBeforeUnmount(() => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  editor.value?.destroy()
})
</script>

<template>
  <div class="doc-editor-wrap" :class="{ readonly: !editable }">
    <div v-if="loading" class="d-flex justify-center py-8">
      <v-progress-circular indeterminate color="primary" size="28" />
    </div>
    <div
      class="doc-editor"
      @keydown="onKeydown"
      @focusout="onBlur"
    >
      <EditorContent v-if="editor" :editor="editor" />
      <!-- Empty-doc hint: a soft placeholder over the blank editor. -->
      <div
        v-if="editor && !loading && rawDoc.trim() === '' && placeholder"
        class="doc-editor__placeholder"
      >
        {{ placeholder }}
      </div>
      <!-- Feishu-style left gutter block handles: ＋ inserts below, ⠿ reorders.
           Edit mode only, exactly like DocPanel. -->
      <DragHandle
        v-if="editor && editable"
        :editor="editor"
        :on-node-change="onNodeChange"
        class="doc-handle"
      >
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
  </div>
</template>

<style scoped>
/* Mirrors DocPanel's editor styling so 项目文档 reads identically to the
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
     (like DocPanel's .doc-page padding, this reserves the room WITHIN the
     editor so the ＋/⠿ handle sits neatly left of the text, never clipped). */
  padding-left: 56px;
}
.doc-editor__placeholder {
  position: absolute;
  top: 0;
  left: 0;
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
  border: 1px solid var(--line, #dcdfe6);
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
  background: var(--bg-2, #f7f8fa);
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
  border-radius: 5px;
  user-select: none;
  transition: background 0.12s ease, color 0.12s ease;
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

/* ---- Document typography: matches DocPanel. ---- */
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
/* 代码块. */
.doc-editor :deep(pre) {
  position: relative;
  background: var(--bg-2, #f7f8fa);
  border: 1px solid var(--line-2, #ececec);
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
/* lowlight token colors — same palette as DocPanel. */
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
