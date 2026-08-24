<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

import { useAppTheme } from '../theme'

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

/** One registry entry, re-defined in place when the app theme flips. */
const THEME_NAME = 'cheesex'

/** Monaco wants token colours as bare hex digits — no leading `#`. */
function tokenColor(name: string, fallback: string): string {
  return resolveColor(`var(--code-${name})`, fallback).slice(1)
}

// Our own theme, built from the app's CSS variables: app background, the brand
// accent for cursor/selection/active-line (with derived alphas), our gray
// gutter, and the shared --code-* syntax palette. Everything here therefore
// follows the active theme — including the syntax colours, which are the same
// values PanelDoc/DocEditor give the lowlight classes, so a snippet looks the
// same in the doc and in the file editor.
//
// ONE theme name for both light and dark: `defineTheme` is keyed by name and
// Monaco is a global registry, so re-defining and re-applying the same name is
// how the editor follows a theme switch. `base` still has to flip — it supplies
// the colours we do not name here (widgets, find-match, bracket pairs), and a
// 'vs' base under a dark background leaves those unreadable.
function defineCheesexTheme(dark: boolean) {
  const bg = resolveColor('var(--surface)', dark ? '#1b1d20' : '#ffffff')
  const fg = resolveColor('var(--text)', dark ? '#d3d6db' : '#2c2c2c')
  const accent = resolveColor('rgb(var(--v-theme-primary))', dark ? '#ffa733' : '#e08a34')
  const muted = resolveColor('var(--muted)', dark ? '#9ca2ab' : '#6b6b6b')
  const faint = resolveColor('var(--faint)', dark ? '#7a808a' : '#cccccc')
  monaco.editor.defineTheme(THEME_NAME, {
    base: dark ? 'vs-dark' : 'vs',
    inherit: true,
    rules: [
      { token: 'comment', foreground: tokenColor('comment', dark ? '#8b929c' : '#8a8f98'), fontStyle: 'italic' },
      { token: 'keyword', foreground: tokenColor('keyword', dark ? '#7fb2f0' : '#0b5cad') },
      { token: 'string', foreground: tokenColor('string', dark ? '#e0996a' : '#a8471c') },
      { token: 'number', foreground: tokenColor('number', dark ? '#5fcb98' : '#0a7a52') },
      { token: 'type', foreground: tokenColor('type', dark ? '#6fcbd9' : '#267f99') },
      { token: 'function', foreground: tokenColor('function', dark ? '#d9bc72' : '#8a6d1b') },
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
      // The slider is a wash of the FOREGROUND over the editor: black-on-light,
      // white-on-dark. Keeping the light values here would make the scrollbar
      // invisible on the dark ground.
      'scrollbarSlider.background': dark ? '#ffffff1f' : '#0000001f',
      'scrollbarSlider.hoverBackground': dark ? '#ffffff33' : '#00000033',
    },
  })
}

const { isDark } = useAppTheme()

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
  defineCheesexTheme(isDark.value) // resolve from live CSS vars now that the DOM/styles exist
  editor = monaco.editor.create(host.value, {
    value: props.modelValue,
    language: langFor(props.filename),
    theme: THEME_NAME,
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

// Follow the app theme. The CSS variables have already changed by the time this
// fires (they hang off <html data-theme>), so re-resolving them here is enough —
// and setTheme is required as well, because defineTheme on the ACTIVE name does
// not repaint an editor that is already using it.
watch(isDark, (dark) => {
  defineCheesexTheme(dark)
  monaco.editor.setTheme(THEME_NAME)
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
