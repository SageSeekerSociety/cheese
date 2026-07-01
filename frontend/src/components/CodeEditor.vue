<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState, type Extension } from '@codemirror/state'
import { keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'

// A CodeMirror 6 code editor. Picks a language from the file extension, emits the
// live text via v-model, and fires `save` on ⌘/Ctrl-S.
const props = withDefaults(
  defineProps<{ modelValue: string; filename?: string; readonly?: boolean }>(),
  { filename: '', readonly: false },
)
const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'save'): void
}>()

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
      EditorView.theme({
        '&': { height: '100%', fontSize: '12.5px' },
        '.cm-scroller': { overflow: 'auto', fontFamily: 'var(--font-mono)' },
        '.cm-gutters': { background: 'transparent', border: 'none' },
      }),
    ],
  })
  view = new EditorView({ state, parent: host.value })
}

onMounted(build)
// Switching file → rebuild (new content + language).
watch(() => props.filename, build)
// External content change (e.g. reload) that didn't come from typing.
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
}
.code-editor :deep(.cm-editor) {
  height: 100%;
}
.code-editor :deep(.cm-editor.cm-focused) {
  outline: none;
}
</style>
