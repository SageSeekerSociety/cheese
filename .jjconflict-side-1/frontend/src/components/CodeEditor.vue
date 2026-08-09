<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

// The actual VS Code editor (Monaco). Vite bundles each language service as a web
// worker; wire them once so IntelliSense/validation run off the main thread.
;(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    if (label === 'json') return new jsonWorker()
    if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker()
    if (label === 'html' || label === 'handlebars' || label === 'razor') return new htmlWorker()
    if (label === 'typescript' || label === 'javascript') return new tsWorker()
    return new editorWorker()
  },
}

// Resolve a CSS color expression (a var, an rgb triplet, hex, …) to #rrggbb by
// letting the browser compute it. Lets our Monaco theme derive from the app's CSS
// variables instead of hardcoded hex — change the brand color, the editor follows.
function resolveColor(expr: string, fallback: string): string {
  try {
    const el = document.createElement('span')
    el.style.color = expr
    el.style.display = 'none'
    document.body.appendChild(el)
    const rgb = getComputedStyle(el).color
    document.body.removeChild(el)
    const m = rgb.match(/\d+(\.\d+)?/g)
    if (!m || m.length < 3) return fallback
    const h = (n: number) => Math.round(n).toString(16).padStart(2, '0')
    return `#${h(+m[0])}${h(+m[1])}${h(+m[2])}`
  } catch {
    return fallback
  }
}

// Our own light theme, built from the app's CSS variables: app background, the
// brand accent for cursor/selection/active-line (with derived alphas), our gray
// gutter. Syntax token colors stay fixed for readability.
function defineCheesexTheme() {
  const bg = resolveColor('var(--surface)', '#ffffff')
  const fg = resolveColor('var(--text)', '#2c2c2c')
  const accent = resolveColor('rgb(var(--v-theme-primary))', '#e08a34')
  const muted = resolveColor('var(--muted)', '#6b6b6b')
  const faint = resolveColor('var(--faint)', '#cccccc')
  monaco.editor.defineTheme('cheesex-light', {
    base: 'vs',
    inherit: true,
    rules: [
      { token: 'comment', foreground: '8a8f98', fontStyle: 'italic' },
      { token: 'keyword', foreground: '0b5cad' },
      { token: 'string', foreground: 'a8471c' },
      { token: 'number', foreground: '0a7a52' },
      { token: 'type', foreground: '267f99' },
      { token: 'function', foreground: '8a6d1b' },
    ],
    colors: {
      'editor.background': bg,
      'editor.foreground': fg,
      'editorLineNumber.foreground': faint,
      'editorLineNumber.activeForeground': muted,
      'editor.lineHighlightBackground': `${accent}0d`, // ~5% brand tint
      'editor.lineHighlightBorder': '#00000000',
      'editor.selectionBackground': `${accent}33`, // ~20%
      'editor.inactiveSelectionBackground': `${accent}1f`,
      'editor.selectionHighlightBackground': `${accent}22`,
      'editorCursor.foreground': accent,
      'editorIndentGuide.background1': `${faint}80`,
      'editorGutter.background': bg,
      'editorWidget.background': bg,
      'scrollbarSlider.background': '#0000001f',
      'scrollbarSlider.hoverBackground': '#00000033',
    },
  })
}

const props = withDefaults(defineProps<{ modelValue: string; filename?: string; readonly?: boolean }>(), {
  filename: '',
  readonly: false,
})
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
    js: 'javascript',
    jsx: 'javascript',
    mjs: 'javascript',
    cjs: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    py: 'python',
    rb: 'ruby',
    go: 'go',
    rs: 'rust',
    java: 'java',
    c: 'c',
    h: 'c',
    cpp: 'cpp',
    cc: 'cpp',
    html: 'html',
    htm: 'html',
    vue: 'html',
    xml: 'xml',
    css: 'css',
    scss: 'scss',
    less: 'less',
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    toml: 'ini',
    ini: 'ini',
    md: 'markdown',
    markdown: 'markdown',
    sh: 'shell',
    bash: 'shell',
    sql: 'sql',
  }
  return map[ext] ?? 'plaintext'
}

onMounted(() => {
  if (!host.value) return
  defineCheesexTheme() // resolve from live CSS vars now that the DOM/styles exist
  editor = monaco.editor.create(host.value, {
    value: props.modelValue,
    language: langFor(props.filename),
    theme: 'cheesex-light',
    readOnly: props.readonly,
    automaticLayout: true, // follow the drawer's resize
    minimap: { enabled: false }, // too cramped for a side drawer
    fontSize: 12.5,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
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
  }
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
  }
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
  background: var(--surface);
}
</style>
