<script setup lang="ts">
import type * as Monaco from 'monaco-editor'
import type { LanguageLink } from '../lib/lsp/link'

/**
 * The AI teammate's change to the file shown (跟随): the passage it replaces and what it puts
 * there, as written so far. While `writing`, the file is unchanged: the passage is struck through
 * and the new text grows under it, never in the text itself (that is the person's, and is saved).
 * Once `landed`, the file shown already holds the change, which is marked for a moment.
 */
export interface AgentEditMark {
  /** Who is writing it, as its label says. */
  name: string
  /** Its avatar colour: a fixed-lightness hex the same in both themes (`avatarColor`). */
  color: string
  /** The passage replaced; null for a write of the whole file. */
  old: string | null
  /** The passage replaced is still being written: where the change goes is not known yet. */
  oldPending: boolean
  text: string
  state: 'writing' | 'landed'
  /** The person follows it: the editor keeps the change in view. */
  follow: boolean
}

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useAppTheme } from '../theme'

// The actual VS Code editor (Monaco) — loaded the first time an editor mounts,
// not when this file is imported. Monaco is 4MB (1MB gzipped), and the panels
// that hold an editor sit on the room page: importing it statically made every
// room download it before showing anything, for a file editor most visits never
// open.
let monaco: typeof Monaco | null = null
let loading: Promise<typeof Monaco> | null = null

function loadMonaco(): Promise<typeof Monaco> {
  loading ??= (async () => {
    const [mod, editorWorker, cssWorker, htmlWorker, jsonWorker, tsWorker] = await Promise.all([
      import('monaco-editor'),
      import('monaco-editor/esm/vs/editor/editor.worker?worker'),
      import('monaco-editor/esm/vs/language/css/css.worker?worker'),
      import('monaco-editor/esm/vs/language/html/html.worker?worker'),
      import('monaco-editor/esm/vs/language/json/json.worker?worker'),
      import('monaco-editor/esm/vs/language/typescript/ts.worker?worker'),
    ])
    // Vite bundles each language service as a web worker; wire them once so
    // IntelliSense/validation run off the main thread.
    ;(self as unknown as { MonacoEnvironment: Monaco.Environment }).MonacoEnvironment = {
      getWorker(_id: string, label: string) {
        if (label === 'json') return new jsonWorker.default()
        if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker.default()
        if (label === 'html' || label === 'handlebars' || label === 'razor') return new htmlWorker.default()
        if (label === 'typescript' || label === 'javascript') return new tsWorker.default()
        return new editorWorker.default()
      },
    }
    monaco = mod
    return mod
  })()
  return loading
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
function defineCheesexTheme(monaco: typeof Monaco, dark: boolean) {
  const bg = resolveColor('var(--surface)', dark ? '#1b1d20' : '#ffffff')
  const fg = resolveColor('var(--text)', dark ? '#d3d6db' : '#2c2c2c')
  const accent = resolveColor('rgb(var(--v-theme-primary))', dark ? '#ffa733' : '#e08a34')
  // Second argument is a fallback for the one path where the var cannot be read
  // (jsdom, SSR, before the stylesheet lands) — a browser always takes the live
  // var, so these are copies of the tokens, not the source of truth. They drifted
  // once already: the light `--faint` fallback used to read `#cccccc` while the
  // token said `#9aa0a8`. If you move a neutral token, move its fallback here too.
  const muted = resolveColor('var(--muted)', dark ? '#aeb4bd' : '#5a5e66')
  const faint = resolveColor('var(--faint)', dark ? '#888ea0' : '#747a82')
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

const props = withDefaults(
  defineProps<{
    modelValue: string
    filename?: string
    readonly?: boolean
    /** 一枚带行号的 chip 指着的那几行：滚到中间并选中，选区的底色就是标记。 */
    lines?: { start: number; end: number } | null
    /**
     * The files are a task's checkout, reachable by its language servers: each file is a model
     * of its own, and a language with a server gets its diagnostics, completion, hover and go to.
     * Without one the editor holds one model and swaps its text from file to file.
     */
    language?: LanguageLink | null
    /**
     * Files of the checkout (`language`) this editor keeps while it shows another — a cell's
     * tabs: going back to one finds its undo history and where it was scrolled as it left them.
     */
    keep?: readonly string[]
    /** The AI teammate's change to this file, while it is written and just after it lands. */
    agent?: AgentEditMark | null
  }>(),
  { filename: '', readonly: false, lines: null, language: null, keep: () => [], agent: null }
)
const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'save'): void
  /** ⌘/Ctrl-P inside the editor: find a file by name (the browser's print dialog is not wanted). */
  (e: 'quick-open'): void
  /** The person clicked, typed or scrolled in the editor: following ends. */
  (e: 'person'): void
}>()

const host = ref<HTMLElement | null>(null)
let editor: Monaco.editor.IStandaloneCodeEditor | null = null
let unmounted = false
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
    cxx: 'cpp',
    hpp: 'cpp',
    hh: 'cpp',
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

// ---- files of a task's checkout (`language`): one model per file, shared with the other editors
// showing it, and kept on the task's language servers (`lib/lsp/monaco`, loaded with Monaco).
// An editor keeps the way it mounted: a link given or taken away later only changes which
// task's models a linked editor shows.
let lsp: typeof import('../lib/lsp/monaco') | null = null
let linked: Monaco.editor.ITextModel | null = null
/** The file `linked` is. */
let linkedPath: string | null = null
/** Models of the files in `keep` this editor showed and still holds, by path. */
const kept = new Map<string, Monaco.editor.ITextModel>()
/** Where each kept file was scrolled, its cursor and folds, by path. */
const views = new Map<string, Monaco.editor.ICodeEditorViewState>()
/** For each kept file, the text it was last given from outside (`modelValue`), by path. */
const given = new Map<string, string>()
let stopOpener: (() => void) | null = null

/** Shows the file's own model in the editor, keeping or letting go of the one it showed before. */
function showLinkedModel() {
  const link = props.language
  if (!editor || !monaco || !lsp || !link) return
  const previous = linked
  const previousPath = linkedPath
  const switching = previousPath !== null && previousPath !== props.filename
  if (switching && previous) {
    const view = editor.saveViewState()
    if (view && props.keep.includes(previousPath)) views.set(previousPath, view)
  }
  let held = kept.get(props.filename) ?? null
  kept.delete(props.filename)
  // Kept for another task's checkout (the cell moved to another task): not this file.
  if (held && held.uri.authority !== lsp.modelUri(monaco, link.scope, props.filename).authority) {
    lsp.closeDocument(held)
    held = null
  }
  const model = held ?? lsp.openDocument(monaco, link, props.filename, langFor(props.filename), props.modelValue)
  linked = model
  linkedPath = props.filename
  applying = true
  // A kept model holds what the person typed since: it is rewritten only when the text
  // given from outside is not the one it was given before (the file changed elsewhere).
  // Rewriting it drops its undo history and where it was scrolled.
  const stale = held ? given.get(props.filename) !== props.modelValue : model.getValue() !== props.modelValue
  if (stale && model.getValue() !== props.modelValue) model.setValue(props.modelValue)
  given.set(props.filename, props.modelValue)
  if (editor.getModel() !== model) editor.setModel(model)
  applying = false
  const view = switching ? views.get(props.filename) : undefined
  if (view) editor.restoreViewState(view)
  if (previous) {
    if (switching && previous !== model && props.keep.includes(previousPath)) kept.set(previousPath, previous)
    else lsp.closeDocument(previous)
  }
  stopOpener?.()
  stopOpener = lsp.openerFor(monaco, editor, link)
}

// A file leaves `keep` (its tab closed): its model and view are let go.
watch(
  () => props.keep,
  (keep) => {
    for (const [path, model] of [...kept]) {
      if (keep.includes(path)) continue
      kept.delete(path)
      lsp?.closeDocument(model)
    }
    for (const path of [...views.keys()]) if (!keep.includes(path)) views.delete(path)
    for (const path of [...given.keys()]) if (!keep.includes(path) && path !== linkedPath) given.delete(path)
  }
)

onMounted(async () => {
  const [monaco, lspModule] = await Promise.all([
    loadMonaco(),
    props.language ? import('../lib/lsp/monaco') : Promise.resolve(null),
  ])
  lsp = lspModule
  // Gone while Monaco was on its way (the reader switched file or tab).
  if (unmounted || !host.value) return
  defineCheesexTheme(monaco, isDark.value) // resolve from live CSS vars now that the DOM/styles exist
  const linkedFiles = !!(lsp && props.language)
  editor = monaco.editor.create(host.value, {
    ...(linkedFiles ? { model: null } : { value: props.modelValue, language: langFor(props.filename) }),
    theme: THEME_NAME,
    readOnly: props.readonly,
    automaticLayout: true, // follow the drawer's resize
    // Hovers and the completion list are laid out against the window, not the editor's box:
    // the editor sits in a narrow panel that clips whatever is wider than it.
    fixedOverflowWidgets: true,
    minimap: { enabled: false }, // too cramped for a side drawer
    fontSize: 12.5,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    lineNumbersMinChars: 3,
    scrollBeyondLastLine: false,
    renderWhitespace: 'selection',
    tabSize: 2,
    padding: { top: 8, bottom: 8 },
    smoothScrolling: true,
    // Monaco's default ambiguous-character table flags full-width ：，；（） as
    // look-alikes of ASCII punctuation, so every Chinese comment is boxed in yellow.
    // The zh tables leave those out (locales intersect) and still flag real
    // look-alikes such as a Cyrillic 'а' in an identifier.
    unicodeHighlight: { allowedLocales: { _os: true, _vscode: true, 'zh-hans': true, 'zh-hant': true } },
  })
  editor.onDidChangeModelContent(() => {
    if (!applying) emit('update:modelValue', editor!.getValue())
  })
  // ⌘/Ctrl-S saves without the browser's save dialog.
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => emit('save'))
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyP, () => emit('quick-open'))
  editor.onMouseDown(() => emit('person'))
  editor.onKeyDown(() => emit('person'))
  editor.onMouseWheel(() => emit('person'))
  agentMarks = editor.createDecorationsCollection()
  if (linkedFiles) showLinkedModel()
  showLines()
  drawAgent()
})

// ---- the AI teammate's change (`agent`, 跟随) ----
let agentMarks: Monaco.editor.IEditorDecorationsCollection | null = null
let agentZone: string | null = null
/** What the zone shows, so a frame that changed nothing redraws nothing. */
let agentZoneKey = ''
/** Where following last brought the change into view: it is not scrolled to again. */
let agentShown = ''

function clearAgentZone() {
  if (!editor || agentZone === null) return
  const id = agentZone
  editor.changeViewZones((zones) => zones.removeZone(id))
  agentZone = null
  agentZoneKey = ''
}

/** The zone under the replaced passage: the new text as written so far, the caret after it. */
function agentZoneNode(mark: AgentEditMark): HTMLElement {
  const node = document.createElement('div')
  node.className = 'agent-zone'
  const text = document.createElement('pre')
  text.className = 'agent-zone__text'
  // One line of the zone is one line of the editor: its height is counted in them.
  // Set in the editor's own type, so the new text stands in its columns.
  if (editor && monaco) {
    const font = editor.getOption(monaco.editor.EditorOption.fontInfo)
    text.style.lineHeight = `${font.lineHeight}px`
    text.style.fontFamily = font.fontFamily
    text.style.fontSize = `${font.fontSize}px`
  }
  text.textContent = mark.text
  const caret = document.createElement('span')
  caret.className = 'agent-zone__caret'
  const flag = document.createElement('span')
  flag.className = 'agent-zone__flag'
  flag.textContent = mark.name
  text.append(caret, flag)
  node.append(text)
  return node
}

function drawAgent() {
  const mark = props.agent
  const model = editor?.getModel()
  if (!editor || !monaco || !model || !agentMarks) return
  host.value?.style.setProperty('--agent', mark?.color ?? 'transparent')
  if (!mark) {
    agentMarks.clear()
    clearAgentZone()
    agentShown = ''
    return
  }
  const content = model.getValue()
  if (mark.state === 'landed') {
    clearAgentZone()
    const at = mark.text ? content.indexOf(mark.text) : -1
    if (at < 0) {
      agentMarks.clear()
      return
    }
    const start = model.getPositionAt(at)
    const end = model.getPositionAt(at + mark.text.length)
    const range = new monaco.Range(start.lineNumber, start.column, end.lineNumber, end.column)
    agentMarks.set([{ range, options: { inlineClassName: 'agent-landed', linesDecorationsClassName: 'agent-line' } }])
    revealAgent(`landed:${at}`, start.lineNumber, end.lineNumber)
    return
  }
  // Writing: where it goes is the passage it replaces (the whole file for a write).
  let from = 0
  let to = content.length
  if (mark.old !== null) {
    const at = mark.old ? content.indexOf(mark.old) : -1
    if (at < 0) {
      agentMarks.clear()
      clearAgentZone()
      return
    }
    from = at
    to = at + mark.old.length
  } else if (mark.oldPending) {
    agentMarks.clear()
    clearAgentZone()
    return
  }
  const start = model.getPositionAt(from)
  const end = model.getPositionAt(to)
  agentMarks.set(
    to > from
      ? [
          {
            range: new monaco.Range(start.lineNumber, start.column, end.lineNumber, end.column),
            options: { inlineClassName: 'agent-replaced', linesDecorationsClassName: 'agent-line' },
          },
        ]
      : []
  )
  const after = to > from ? end.lineNumber : Math.max(0, start.lineNumber - 1)
  const lines = Math.max(1, mark.text.split('\n').length)
  const key = `${after}:${lines}:${mark.name}:${mark.text}`
  if (key !== agentZoneKey) {
    clearAgentZone()
    const node = agentZoneNode(mark)
    editor.changeViewZones((zones) => {
      agentZone = zones.addZone({ afterLineNumber: after, heightInLines: lines, domNode: node })
    })
    agentZoneKey = key
  }
  revealAgent(`writing:${from}:${lines}`, start.lineNumber, after + lines)
}

/** Following: bring the change into view, once for each place it moves to. */
function revealAgent(where: string, first: number, last: number) {
  if (!editor || !monaco || !props.agent?.follow || where === agentShown) return
  agentShown = where
  editor.revealRangeInCenterIfOutsideViewport(new monaco.Range(first, 1, last, 1), monaco.editor.ScrollType.Smooth)
}

watch(
  () => [props.agent, props.modelValue, props.filename] as const,
  () => drawAgent(),
  { flush: 'post' }
)

function showLines() {
  const lines = props.lines
  if (!editor || !lines) return
  editor.setSelection({ startLineNumber: lines.start, startColumn: 1, endLineNumber: lines.end + 1, endColumn: 1 })
  editor.revealLinesInCenter(lines.start, lines.end)
}
watch(() => props.lines, showLines)

// Follow the app theme. The CSS variables have already changed by the time this
// fires (they hang off <html data-theme>), so re-resolving them here is enough —
// and setTheme is required as well, because defineTheme on the ACTIVE name does
// not repaint an editor that is already using it.
watch(isDark, (dark) => {
  if (!monaco) return
  defineCheesexTheme(monaco, dark)
  monaco.editor.setTheme(THEME_NAME)
})

// Switching file → swap content + language, keeping one editor instance; with a language link,
// the file's own model instead.
watch(
  () => [props.filename, props.language] as const,
  ([filename, link], [prevFilename]) => {
    if (!editor || !monaco) return
    if (linked) {
      if (link) showLinkedModel()
      showLines()
      return
    }
    if (filename === prevFilename) return
    applying = true
    editor.setValue(props.modelValue)
    const model = editor.getModel()
    if (model) monaco.editor.setModelLanguage(model, langFor(props.filename))
    applying = false
    showLines()
  }
)
// External content change that didn't come from typing (e.g. reload).
watch(
  () => props.readonly,
  (readOnly) => editor?.updateOptions({ readOnly })
)

watch(
  () => props.modelValue,
  (v) => {
    // The text of another file, arriving a moment before its name: that file's own model
    // takes it (`showLinkedModel`), not the one still on screen.
    if (linked && props.filename !== linkedPath) return
    if (linkedPath !== null) given.set(linkedPath, v)
    if (editor && v !== editor.getValue()) {
      applying = true
      editor.setValue(v)
      applying = false
    }
  }
)
onBeforeUnmount(() => {
  unmounted = true
  stopOpener?.()
  editor?.dispose()
  if (linked) lsp?.closeDocument(linked)
  for (const model of kept.values()) lsp?.closeDocument(model)
  kept.clear()
})
</script>

<template>
  <div ref="host" class="code-editor" />
</template>

<!-- Monaco draws these outside the component's own elements: they cannot be scoped. The agent's
     colour is its avatar's, a fixed-lightness hex the same in both themes (`--agent`, set on the
     editor), so the washes mixed from it do not follow the theme either; the text over them is
     the editor's own and does. -->
<style>
.code-editor .agent-replaced {
  text-decoration: line-through;
  opacity: 0.55;
}
.code-editor .agent-landed {
  background: color-mix(in srgb, var(--agent) 22%, transparent);
}
.code-editor .agent-line {
  width: 3px !important;
  margin-left: 3px;
  background: var(--agent);
}
.code-editor .agent-zone {
  background: color-mix(in srgb, var(--agent) 12%, transparent);
}
.code-editor .agent-zone__text {
  margin: 0;
  color: var(--text);
  font-variant-ligatures: none;
  white-space: pre;
}
.code-editor .agent-zone__caret {
  display: inline-block;
  width: 2px;
  height: 1.1em;
  margin-left: 1px;
  vertical-align: text-bottom;
  background: var(--agent);
}
.code-editor .agent-zone__flag {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--agent);
  color: var(--avatar-ink);
  font-family: var(--font-sans);
  font-size: 12px;
  font-weight: 600;
  vertical-align: text-bottom;
}
</style>

<style scoped>
.code-editor {
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
}
</style>
