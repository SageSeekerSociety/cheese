<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

// The actual VS Code editor (Monaco). Vite bundles each language service as a web
// worker; wire them once so IntelliSense/validation run off the main thread.
;(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    if (label === 'json') return new jsonWorker()
    if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker()
    if (label === 'html' || label === 'handlebars' || label === 'razor')
      return new htmlWorker()
    if (label === 'typescript' || label === 'javascript') return new tsWorker()
    return new editorWorker()
  },
}

const props = withDefaults(
  defineProps<{ modelValue: string; filename?: string; readonly?: boolean }>(),
  { filename: '', readonly: false },
)
const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'save'): void
}>()

const host = ref<HTMLElement | null>(null)
let editor: monaco.editor.IStandaloneCodeEditor | null = null
let applying = false // guard so programmatic setValue doesn't echo back as an edit

function langFor(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
    ts: 'typescript', tsx: 'typescript',
    py: 'python', rb: 'ruby', go: 'go', rs: 'rust', java: 'java',
    c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp',
    html: 'html', htm: 'html', vue: 'html', xml: 'xml',
    css: 'css', scss: 'scss', less: 'less',
    json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'ini', ini: 'ini',
    md: 'markdown', markdown: 'markdown', sh: 'shell', bash: 'shell', sql: 'sql',
  }
  return map[ext] ?? 'plaintext'
}

onMounted(() => {
  if (!host.value) return
  editor = monaco.editor.create(host.value, {
    value: props.modelValue,
    language: langFor(props.filename),
    theme: 'vs-dark',
    readOnly: props.readonly,
    automaticLayout: true, // follow the drawer's resize
    minimap: { enabled: false }, // too cramped for a side drawer
    fontSize: 12.5,
    fontFamily:
      'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    lineNumbersMinChars: 3,
    scrollBeyondLastLine: false,
    renderWhitespace: 'selection',
    tabSize: 2,
    padding: { top: 8, bottom: 8 },
    smoothScrolling: true,
  })
  editor.onDidChangeModelContent(() => {
    if (!applying) emit('update:modelValue', editor!.getValue())
  })
  // ⌘/Ctrl-S saves without the browser's save dialog.
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => emit('save'))
})

// Switching file → swap content + language, keeping one editor instance.
watch(
  () => props.filename,
  () => {
    if (!editor) return
    applying = true
    editor.setValue(props.modelValue)
    const model = editor.getModel()
    if (model) monaco.editor.setModelLanguage(model, langFor(props.filename))
    applying = false
  },
)
// External content change that didn't come from typing (e.g. reload).
watch(
  () => props.modelValue,
  (v) => {
    if (editor && v !== editor.getValue()) {
      applying = true
      editor.setValue(v)
      applying = false
    }
  },
)
onBeforeUnmount(() => editor?.dispose())
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
</style>
