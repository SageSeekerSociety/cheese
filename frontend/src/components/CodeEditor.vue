<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState, type Extension } from '@codemirror/state'
import { keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'

// A CodeMirror 6 editor styled like VS Code's Dark+ theme. Picks a language from
// the file extension, emits the live text via v-model, and saves on ⌘/Ctrl-S.
const props = withDefaults(
  defineProps<{ modelValue: string; filename?: string; readonly?: boolean }>(),
  { filename: '', readonly: false },
)
const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'save'): void
}>()

// --- VS Code Dark+ ---------------------------------------------------------
const vscodeTheme = EditorView.theme(
  {
    '&': { color: '#d4d4d4', backgroundColor: '#1e1e1e', height: '100%' },
    '.cm-content': {
      caretColor: '#aeafad',
      fontFamily: 'var(--font-mono)',
      fontSize: '12.5px',
    },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: '#aeafad' },
    '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':
      { backgroundColor: '#264f78' },
    '.cm-activeLine': { backgroundColor: '#ffffff0a' },
    '.cm-gutters': {
      backgroundColor: '#1e1e1e',
      color: '#858585',
      border: 'none',
    },
    '.cm-activeLineGutter': { backgroundColor: '#ffffff0a', color: '#c6c6c6' },
    '.cm-lineNumbers .cm-gutterElement': { padding: '0 10px 0 14px' },
    '.cm-selectionMatch': { backgroundColor: '#3a3d41' },
    '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': {
      backgroundColor: '#0d3a58',
      outline: '1px solid #5a5a5a',
    },
    '.cm-scroller': { overflow: 'auto', lineHeight: '1.55' },
    '.cm-foldPlaceholder': {
      backgroundColor: '#2b2b2b',
      border: 'none',
      color: '#8c8c8c',
    },
  },
  { dark: true },
)

const vscodeHighlight = HighlightStyle.define([
  { tag: [t.keyword, t.modifier, t.operatorKeyword], color: '#569cd6' },
  { tag: [t.controlKeyword, t.moduleKeyword], color: '#c586c0' },
  { tag: [t.name, t.deleted, t.character, t.macroName], color: '#9cdcfe' },
  { tag: [t.propertyName], color: '#9cdcfe' },
  { tag: [t.variableName], color: '#9cdcfe' },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: '#dcdcaa' },
  { tag: [t.labelName], color: '#dcdcaa' },
  { tag: [t.typeName, t.className, t.namespace], color: '#4ec9b0' },
  { tag: [t.tagName], color: '#569cd6' },
  { tag: [t.attributeName], color: '#9cdcfe' },
  { tag: [t.number, t.bool, t.null, t.atom], color: '#b5cea8' },
  { tag: [t.string, t.special(t.string), t.regexp], color: '#ce9178' },
  { tag: [t.escape], color: '#d7ba7d' },
  { tag: [t.comment, t.lineComment, t.blockComment], color: '#6a9955', fontStyle: 'italic' },
  { tag: [t.operator, t.punctuation, t.separator, t.bracket], color: '#d4d4d4' },
  { tag: [t.meta, t.processingInstruction], color: '#569cd6' },
  { tag: [t.heading], color: '#569cd6', fontWeight: 'bold' },
  { tag: [t.strong], fontWeight: 'bold' },
  { tag: [t.emphasis], fontStyle: 'italic' },
  { tag: [t.link, t.url], color: '#ce9178', textDecoration: 'underline' },
  { tag: [t.invalid], color: '#f44747' },
])

const host = ref<HTMLElement | null>(null)
let view: EditorView | null = null

function langFor(name: string): Extension[] {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['js', 'jsx', 'mjs', 'cjs'].includes(ext)) return [javascript({ jsx: true })]
  if (ext === 'ts') return [javascript({ typescript: true })]
  if (ext === 'tsx') return [javascript({ typescript: true, jsx: true })]
  if (ext === 'py') return [python()]
  if (['html', 'htm', 'vue'].includes(ext)) return [html()]
  if (['css', 'scss', 'less'].includes(ext)) return [css()]
  if (ext === 'json') return [json()]
  if (['md', 'markdown'].includes(ext)) return [markdown()]
  return []
}

function build() {
  view?.destroy()
  if (!host.value) return
  const state = EditorState.create({
    doc: props.modelValue,
    extensions: [
      basicSetup,
      vscodeTheme,
      syntaxHighlighting(vscodeHighlight),
      keymap.of([
        indentWithTab,
        { key: 'Mod-s', preventDefault: true, run: () => (emit('save'), true) },
      ]),
      ...langFor(props.filename),
      EditorView.editable.of(!props.readonly),
      EditorState.readOnly.of(props.readonly),
      EditorView.updateListener.of((u) => {
        if (u.docChanged) emit('update:modelValue', u.state.doc.toString())
      }),
    ],
  })
  view = new EditorView({ state, parent: host.value })
}

onMounted(build)
watch(() => props.filename, build)
watch(
  () => props.modelValue,
  (v) => {
    if (view && v !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: v } })
    }
  },
)
onBeforeUnmount(() => view?.destroy())
</script>

<template>
  <div ref="host" class="code-editor" />
</template>

<style scoped>
.code-editor {
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #1e1e1e;
}
.code-editor :deep(.cm-editor) {
  height: 100%;
}
.code-editor :deep(.cm-editor.cm-focused) {
  outline: none;
}
</style>
