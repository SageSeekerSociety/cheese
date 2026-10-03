// Pure logic for the hardcoded-Chinese gate. No I/O, no process — so it can be
// tested directly (see i18n-cjk-gate-core.test.mjs, run by `node --test` in CI).
//
// WHY THIS EXISTS, AND WHY IT IS NOT catalog.spec.ts:
// `catalog.spec.ts` checks the catalog against itself — every `zh-CN` key has
// English, every key has a call site, no English value contains Chinese. All of
// that is scoped to keys that were *already extracted*. A Chinese string typed
// straight into a template is invisible to every one of those checks, so the
// suite can be fully green while the English UI is still Chinese.
//
// THE RULE: no line in scope may carry Chinese the user can read, with one way
// out — Chinese that is data rather than copy (compared, parsed or stored, never
// shown as interface text) stays, and says so on the same line:
//
//   ['a', '阿'], // i18n-data: collation anchor, compared and never shown
//   <span>中</span> <!-- i18n-data: … -->
//
// The exception lives on the line it excuses, so it is read in the diff that
// adds it and deleted with the line. A list kept in another file would outlive
// the lines it names and let a new string slip in under an old entry.

/**
 * The same character class `catalog.spec.ts` uses for "this English value still
 * has Chinese in it" (U+3400–4DBF, U+4E00–9FFF, U+F900–FAFF). Deliberately one
 * definition of "Chinese" across both gates, so a string can never be Chinese
 * to one and not the other.
 *
 * Written as escapes on purpose. A literal U+F900 in the source is normalised
 * to U+8C48 by NFC, which silently turned the last range into U+8C48–U+FAFF:
 * Hangul, the UTF-16 surrogates of every emoji, and the private-use area.
 *
 * Note it excludes CJK punctuation (U+3000–303F, U+FF00–FFEF). That is fine
 * here as everywhere: every real Chinese sentence carries a Han character, and
 * a string made of nothing but 「——」 is not text anyone translates.
 */
export const CJK = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/

/** Paths the gate never looks at. */
export function shouldScan(relPath) {
  const p = String(relPath).replaceAll('\\', '/')
  if (!/\.(vue|ts|js)$/.test(p)) return false
  // `src/i18n/` is the catalog itself: `messages/zh-CN/**` is Chinese by
  // definition, and `languages.ts` holds 「中文」/「English」 on purpose — the
  // language names must read the same under every locale so someone who cannot
  // read the current one can still find their way back (docs/i18n.md §5).
  if (p.startsWith('src/i18n/')) return false
  // Two builds that are not the product. `src/proto-*` feeds the preview-only
  // entries (feedback-proto.html, dashboard-proto.html) that `vite build` does
  // not include; `views/demo/` (and its entry, `demo-main.ts`) is the component
  // catalog developers browse and the scripted demos embedded in the manual,
  // which is Chinese by decision (docs/i18n.md §1). Their Chinese is sample
  // content, not interface copy, and counting it would bury the real number.
  if (/^src\/proto-/.test(p) || p.startsWith('src/views/demo/') || p === 'src/demo-main.ts') return false
  if (/(^|\/)__tests__\//.test(p)) return false
  if (/\.(spec|test)\.(ts|js)$/.test(p)) return false
  return true
}

/**
 * Offsets of every string literal's *contents* in a JS/TS chunk.
 *
 * A hand-written scanner rather than a parser on purpose: docs/i18n.md §5 says
 * the gates carry zero new dependencies, and `@vue/compiler-sfc` is not hoisted
 * even though vue pulls it in transitively (checked: `require.resolve` fails
 * from frontend/). This is the whole reason a parser is not used, not a
 * preference for hand-rolling one.
 *
 * Comment bodies are excluded because Chinese prose in a comment is not UI
 * copy and there is a lot of it in this tree — counting it would drown the
 * signal and freeze thousands of lines nobody is going to translate.
 * @param {string} code
 * @returns {Array<{start: number, end: number, open?: number}>} `open`, on a
 *   template's pieces, is where that template's text starts (see `scanTemplate`)
 */
export function stringSpans(code) {
  const src = String(code)
  const spans = []
  const n = src.length
  let i = 0
  // Last significant token outside comments and strings, used only to decide
  // whether a `/` opens a regex or is division. Both are wrong sometimes; the
  // cost of being wrong is a desynced scanner on that one file, which reports
  // comment Chinese as copy (loud) or reads a string as code (a miss).
  let prevChar = ''
  let prevWord = ''
  let word = ''

  const REGEX_KEYWORDS = new Set([
    'return',
    'typeof',
    'instanceof',
    'in',
    'of',
    'new',
    'delete',
    'void',
    'case',
    'do',
    'else',
    'yield',
    'await',
  ])
  const REGEX_PUNCT = new Set([
    '(',
    ',',
    '=',
    ':',
    '[',
    '!',
    '&',
    '|',
    '?',
    '{',
    '}',
    ';',
    '<',
    '>',
    '+',
    '-',
    '*',
    '%',
    '^',
    '~',
  ])

  const pushWord = () => {
    if (word) prevWord = word
    word = ''
  }

  while (i < n) {
    const c = src[i]

    if (c === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i)
      i = nl === -1 ? n : nl
      continue
    }
    if (c === '/' && src[i + 1] === '*') {
      const close = src.indexOf('*/', i + 2)
      i = close === -1 ? n : close + 2
      continue
    }
    if (c === '/' && (prevChar === '' || REGEX_PUNCT.has(prevChar) || REGEX_KEYWORDS.has(prevWord))) {
      i = skipRegex(src, i)
      prevChar = 'x'
      prevWord = ''
      continue
    }

    if (c === '"' || c === "'") {
      const end = skipQuoted(src, i)
      spans.push({ start: i + 1, end: Math.max(i + 1, end - 1) })
      i = end
      prevChar = 'x'
      prevWord = ''
      continue
    }

    if (c === '`') {
      i = scanTemplate(src, i, spans, i + 1)
      prevChar = 'x'
      prevWord = ''
      continue
    }

    if (/[A-Za-z0-9_$]/.test(c)) {
      word += c
      prevChar = c
      i++
      continue
    }
    if (/\s/.test(c)) {
      i++
      continue
    }
    pushWord()
    prevChar = c
    i++
  }
  return spans
}

/** Body of a regex literal, plus any character class. Returns the index after it. */
function skipRegex(src, start) {
  let i = start + 1
  let inClass = false
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') {
      i += 2
      continue
    }
    if (c === '\n') return i // unterminated; treat the line end as the end
    if (c === '[') inClass = true
    else if (c === ']') inClass = false
    else if (c === '/' && !inClass) {
      i++
      // flags
      while (i < src.length && /[a-z]/i.test(src[i])) i++
      return i
    }
    i++
  }
  return src.length
}

/** Index just past the closing quote. Unbalanced quotes end at the line end. */
function skipQuoted(src, start) {
  const quote = src[start]
  let i = start + 1
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') {
      i += 2
      continue
    }
    if (c === quote) return i + 1
    if (c === '\n') return i
    i++
  }
  return src.length
}

/**
 * Pushes the literal chunks of the template starting at `start` onto `spans`
 * and returns the index just past its closing backtick. The text around each
 * `${…}` is the literal — a string built as `` `剩余 ${n} 天` `` is exactly the
 * case this gate is for — and the code inside is scanned as code, so a comment
 * there is a comment and a string there is a string of its own.
 *
 * `open` is where the outermost template's text starts. Every chunk and every
 * string nested in it carries it, because whether a template is a developer log
 * is decided by what precedes its opening backtick (see `isDevLog`), not by
 * what precedes a chunk after `}`.
 */
function scanTemplate(src, start, spans, open) {
  let chunk = start + 1
  let i = start + 1
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') {
      i += 2
      continue
    }
    if (c === '`') {
      spans.push({ start: chunk, end: i, open })
      return i + 1
    }
    if (c === '$' && src[i + 1] === '{') {
      spans.push({ start: chunk, end: i, open })
      i = scanInterpolation(src, i + 2, spans, open)
      chunk = i
      continue
    }
    i++
  }
  spans.push({ start: chunk, end: src.length, open })
  return src.length
}

/**
 * The code inside a `${…}`, from just past its brace. Returns the index just
 * past the brace that closes it: braces are counted, so an object literal in
 * there (`${query({ a, b })}`) does not end it early.
 */
function scanInterpolation(src, start, spans, open) {
  let depth = 0
  let i = start
  while (i < src.length) {
    const c = src[i]
    if (c === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i)
      i = nl === -1 ? src.length : nl
      continue
    }
    if (c === '/' && src[i + 1] === '*') {
      const close = src.indexOf('*/', i + 2)
      i = close === -1 ? src.length : close + 2
      continue
    }
    if (c === '"' || c === "'") {
      const end = skipQuoted(src, i)
      spans.push({ start: i + 1, end: Math.max(i + 1, end - 1), open })
      i = end
      continue
    }
    if (c === '`') {
      i = scanTemplate(src, i, spans, open)
      continue
    }
    if (c === '{') depth++
    else if (c === '}') {
      if (depth === 0) return i + 1
      depth--
    }
    i++
  }
  return src.length
}

/** 1-based line number of each offset, given the line-start offsets. */
function lineIndexer(text) {
  const starts = [0]
  for (let i = 0; i < text.length; i++) if (text[i] === '\n') starts.push(i + 1)
  return (offset) => {
    let lo = 0
    let hi = starts.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (starts[mid] <= offset) lo = mid
      else hi = mid - 1
    }
    return lo + 1
  }
}

/**
 * Lines of `text` that carry Chinese the user can end up reading.
 *
 * The unit is a **line**, not an occurrence: it is what an annotation sits on,
 * and what a person reads in the report. 「剩余 ${n} 天」 is one finding, not two.
 * @param {string} relPath
 * @param {string} text
 * @returns {number[]} sorted, de-duplicated, 1-based line numbers
 */
export function scanSource(relPath, text) {
  return [...chineseLines(relPath, text).keys()].sort((a, b) => a - b)
}

/**
 * The same lines as `scanSource`, each with the comment syntax that can annotate
 * it: `'markup'` for a `.vue` template line (only `<!-- -->` is a comment there —
 * `//` is text the user would see), `'code'` for a script or `.ts` line.
 * @returns {Map<number, 'markup' | 'code'>}
 */
function chineseLines(relPath, text) {
  const src = String(text)
  const path = String(relPath).replaceAll('\\', '/')
  const lineOf = lineIndexer(src)
  /** @type {Map<number, 'markup' | 'code'>} */
  const found = new Map()
  // A string literal may span lines (template literals do, and so do strings
  // with a trailing backslash), so a span contributes every line *inside it*
  // that actually carries Chinese — not just the line it starts on, and not the
  // blank lines in between.
  // `base` is where `code` starts inside `src` — 0 for a .ts file, the offset
  // of the block's body for a .vue one. Offsets are line-mapped against the
  // whole file so the reported line number is the one an editor shows.
  const addSpan = (code, span, base) => {
    if (isDevLog(code, span.open ?? span.start)) return
    const body = code.slice(span.start, span.end)
    body.split('\n').forEach((line, index) => {
      if (CJK.test(line)) found.set(lineOf(base + span.start) + index, 'code')
    })
  }

  if (path.endsWith('.vue')) {
    for (const template of topLevelBlocks(src, 'template')) {
      // In a template, Chinese anywhere outside an HTML comment is copy: text
      // nodes and attribute values alike (`label="真实姓名"`,
      // `placeholder="请输入您的学号"`). Tag and attribute *names* cannot hold
      // Han characters, so there is nothing to exclude.
      const stripped = template.body.replace(/<!--[\s\S]*?-->/g, (m) => m.replace(/[^\n]/g, ' '))
      for (const line of cjkLines(stripped)) found.set(lineOf(template.start) + line - 1, 'markup')
    }
    for (const script of topLevelBlocks(src, 'script')) {
      for (const span of stringSpans(script.body)) addSpan(script.body, span, script.start)
    }
    // `<style>` is deliberately not scanned: the catalog cannot reach CSS, and
    // the only Chinese that ever appears there is a `content:` string, which is
    // a different (and currently non-existent) problem.
  } else {
    for (const span of stringSpans(src)) addSpan(src, span, 0)
  }

  return found
}

/** An exempting comment: the marker right after the comment opener, then the reason. */
const ANNOTATIONS = {
  code: [/\/\/\s*i18n-data:(.*)$/, /\/\*\s*i18n-data:(.*?)(?:\*\/|$)/],
  markup: [/<!--\s*i18n-data:(.*?)(?:-->|$)/],
}

/**
 * The `i18n-data:` annotation on one line, read with the comment syntaxes in
 * `kinds`. `null` when there is none; otherwise its reason, trimmed — which may
 * be empty, and that is a failure of its own.
 * @param {string} line
 * @param {Array<'markup' | 'code'>} kinds
 * @returns {{ reason: string } | null}
 */
export function annotationOn(line, kinds) {
  for (const kind of kinds) {
    for (const pattern of ANNOTATIONS[kind]) {
      const m = pattern.exec(line)
      if (m) return { reason: m[1].trim() }
    }
  }
  return null
}

/** A reason says something: at least one letter or digit, not just `—` or `<>`. */
const hasReason = (reason) => /[\p{L}\p{N}]/u.test(reason)

/**
 * Everything wrong with one file, line by line:
 *   `copy`       — Chinese with no annotation: it belongs in the catalog
 *   `no-reason`  — an annotation that does not say why
 *   `stale`      — an annotation on a line with no Chinese to excuse (the string
 *                  it covered moved or was translated); left in place it would
 *                  read as an exemption for whatever lands on that line next
 * plus the lines that are correctly annotated, so the run can say how many.
 * @param {string} relPath
 * @param {string} text
 * @returns {{ problems: Array<{line: number, problem: 'copy' | 'no-reason' | 'stale', text: string}>, annotated: number[] }}
 */
export function checkSource(relPath, text) {
  const found = chineseLines(relPath, text)
  const lines = String(text).split('\n')
  const problems = []
  const annotated = []
  lines.forEach((raw, index) => {
    const line = index + 1
    const kind = found.get(line)
    const note = annotationOn(raw, kind ? [kind] : ['code', 'markup'])
    const shown = raw.trim().slice(0, 110)
    if (note && !hasReason(note.reason)) problems.push({ line, problem: 'no-reason', text: shown })
    else if (note && !kind) problems.push({ line, problem: 'stale', text: shown })
    else if (kind && !note) problems.push({ line, problem: 'copy', text: shown })
    else if (note) annotated.push(line)
  })
  return { problems, annotated }
}

/**
 * True when the string starting at `spanStart` is an argument to `console.*` /
 * `logger.*` — a developer log, not copy.
 *
 * `console.error('加载讨论数据失败', e)` is not something a reader of the UI
 * ever sees, so counting it would inflate the headline number with lines nobody
 * is going to translate, and turning one into a `t()` call is actively wrong:
 * it would translate a message that appears in the console of a developer
 * debugging in English. Detected by looking back over the callee name rather
 * than by parsing, which is enough for `console.x('…')` and silently counts the
 * string when the shape is anything more interesting (`console.x('a' + '中文')`).
 */
function isDevLog(code, spanStart) {
  // A span starts *after* its opening quote, so the character to look at is the
  // one before that quote.
  let i = spanStart - 2
  while (i >= 0 && /\s/.test(code[i])) i--
  if (code[i] !== '(') return false
  let j = i - 1
  let name = ''
  while (j >= 0 && /[\w$.?]/.test(code[j])) {
    name = code[j] + name
    j--
  }
  return /(^|[^\w$.])(console|logger)(\?\.[\w$]+|\.[\w$]+)*$/.test(name)
}

/** Line numbers within `text` that contain a Han character. */
function cjkLines(text) {
  const out = []
  text.split('\n').forEach((line, index) => {
    if (CJK.test(line)) out.push(index + 1)
  })
  return out
}

/**
 * Every top-level `<tag>…</tag>` block of a `.vue` file, in file order, with its
 * content offset.
 *
 * An SFC's own blocks open and close at column 0; anything indented is inside
 * one of them. Anchoring on that is what keeps the two look-alikes out: an
 * inner `<template #slot>` (indented, so it stays part of the outer template)
 * and a `<template #…>` or `</script>` written inside a comment or a string (not
 * at column 0, so it is not a block boundary). A file may carry two script
 * blocks — `<script>` beside `<script setup>` — and both are read.
 */
function topLevelBlocks(src, tag) {
  const blocks = []
  const opener = new RegExp(`^<${tag}(?=[\\s>])`, 'gm')
  const closer = new RegExp(`^</${tag}>`, 'gm')
  for (let m = opener.exec(src); m; m = opener.exec(src)) {
    const openEnd = src.indexOf('>', m.index)
    if (openEnd === -1) break
    closer.lastIndex = openEnd
    // A block closed on its own opening line (`<template><div/></template>`) has
    // no column-0 close; take the last one in the file rather than skip it, so a
    // file in an unusual shape is over-read, never silently unread.
    const close = closer.exec(src) ?? { index: src.lastIndexOf(`</${tag}>`) }
    if (close.index < openEnd) break
    // `.vue` blocks are wrapped in a newline; dropping it keeps line numbers
    // right for every line after the first one in the block.
    let start = openEnd + 1
    if (src[start] === '\n') start++
    blocks.push({ start, body: src.slice(start, close.index) })
    opener.lastIndex = close.index
  }
  return blocks
}

/**
 * The whole tree's result: problems per file (files with none are left out) and
 * how many lines carry a valid annotation.
 * @param {Array<{path: string, text: string}>} files
 */
export function scanTree(files) {
  /** @type {Record<string, ReturnType<typeof checkSource>['problems']>} */
  const problems = {}
  let annotated = 0
  for (const { path, text } of files) {
    const rel = String(path).replaceAll('\\', '/')
    if (!shouldScan(rel)) continue
    const result = checkSource(rel, text)
    if (result.problems.length) problems[rel] = result.problems
    annotated += result.annotated.length
  }
  const sorted = Object.fromEntries(Object.entries(problems).sort(([a], [b]) => a.localeCompare(b)))
  return { ok: Object.keys(sorted).length === 0, problems: sorted, annotated }
}

const LABELS = {
  copy: 'Chinese outside the catalog',
  'no-reason': 'i18n-data annotation without a reason',
  stale: 'i18n-data annotation on a line with no Chinese',
}

/**
 * What the gate prints. A failure lists every offending line and, for each kind
 * of problem present, exactly what to do about it.
 * @param {ReturnType<typeof scanTree>} result
 */
export function formatReport(result) {
  const lines = []
  if (result.ok) {
    lines.push(`No hardcoded Chinese in src/. ${result.annotated} line(s) carry an i18n-data annotation.`)
    return lines.join('\n')
  }
  const kinds = new Set()
  let total = 0
  for (const [file, problems] of Object.entries(result.problems)) {
    lines.push(file)
    for (const { line, problem, text } of problems) {
      lines.push(`  ${line}: [${LABELS[problem]}] ${text}`)
      kinds.add(problem)
      total++
    }
  }
  lines.push('')
  if (kinds.has('copy')) {
    lines.push('Chinese a user can read goes through the catalog:')
    lines.push('  1. add the key to src/i18n/messages/zh-CN/<namespace>.json and en/<namespace>.json')
    lines.push("  2. call it: t('namespace.component.role') in script, {{ t('…') }} or :label=\"t('…')\" in a template")
    lines.push('  Key naming: docs/i18n.md §3. English for platform terms: docs/i18n-glossary.md.')
    lines.push('Chinese that is data, not copy (compared, parsed or stored; never shown as interface text),')
    lines.push('stays where it is with an annotation on the SAME line that says why:')
    lines.push(
      "  .ts / <script>   const ORIGIN = '【第 N 页】' // i18n-data: marker stored in task text and parsed back"
    )
    lines.push('  <template>       <span>例</span> <!-- i18n-data: <why this is data> -->')
    lines.push('  Reviewers judge the reason. A line inside a multi-line template literal cannot carry one:')
    lines.push('  split the string so the Chinese sits on a line that can.')
  }
  if (kinds.has('no-reason')) {
    lines.push('An i18n-data annotation must say why the Chinese is data: write the reason after the colon.')
  }
  if (kinds.has('stale')) {
    lines.push('An i18n-data annotation on a line with no counted Chinese excuses nothing: delete it.')
  }
  lines.push('Comments, console/logger arguments and *.spec.ts / __tests__ files are not scanned (docs/i18n.md §5).')
  lines.push(`${total} problem(s).`)
  return lines.join('\n')
}
