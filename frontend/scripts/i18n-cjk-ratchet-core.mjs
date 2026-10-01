// Pure logic for the hardcoded-Chinese ratchet. No I/O, no process — so it can
// be tested directly (see i18n-cjk-ratchet-core.test.mjs, run by `node --test`
// in CI). The scanner is the one PR #1167 proposed, with the multi-`<script>`
// fix from PR #1223.
//
// WHY THIS EXISTS, AND WHY IT IS NOT catalog.spec.ts:
// `catalog.spec.ts` checks the catalog against itself — every `zh-CN` key has an
// `en` one or sits in `untranslated.json`, every key has a call site, no English
// value contains Chinese. All of that is scoped to keys that were *already
// extracted*. A Chinese string typed straight into a template is invisible to
// every one of those checks, so the suite can be fully green while the English
// UI is still Chinese.
//
// WHY A RATCHET AND NOT A PLAIN GATE: the tree holds well over a thousand such
// lines, several people are paying them down in parallel, and a rule that goes
// red everywhere on day one gets switched off rather than fixed (the same
// reasoning as tsc-baseline.json and stylelint-baseline.json). The baseline is a
// per-file CEILING: a file may drop below it — someone else's PR translated it —
// and still pass; only a count above it fails.

// `compare` and `tightenedBaseline` are the tsc ratchet's, so the four ratchets
// under scripts/ share one definition of "regressed" and one of "an --update can
// never loosen a count".
export { compare, tightenedBaseline } from './tsc-ratchet-core.mjs'

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

/** Paths the ratchet never looks at. */
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
 * @returns {Array<{start: number, end: number}>}
 */
export function stringSpans(code) {
  const src = String(code)
  const spans = []
  const n = src.length
  let i = 0
  // Last significant token outside comments and strings, used only to decide
  // whether a `/` opens a regex or is division. Both are wrong sometimes; the
  // cost of being wrong is a desynced scanner on that one file, and the
  // per-file baseline turns that into a visible regression rather than a
  // silently missing count.
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
      const end = skipTemplate(src, i)
      spans.push({ start: i + 1, end: Math.max(i + 1, end - 1) })
      i = end
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
 * Index just past the closing backtick. `${…}` is treated as part of the
 * literal — including nested backticks, which are tracked by depth — because a
 * string built as `` `剩余 ${n} 天` `` is exactly the case this gate is for.
 */
function skipTemplate(src, start) {
  let i = start + 1
  let depth = 0
  while (i < src.length) {
    const c = src[i]
    if (c === '\\') {
      i += 2
      continue
    }
    if (c === '$' && src[i + 1] === '{') {
      depth++
      i += 2
      continue
    }
    if (depth > 0) {
      if (c === '}') depth--
      else if (c === '`') i = skipTemplate(src, i) - 1
      else if (c === '"' || c === "'") i = skipQuoted(src, i) - 1
      else if (c === '/' && src[i + 1] === '/') {
        const nl = src.indexOf('\n', i)
        i = nl === -1 ? src.length - 1 : nl - 1
      }
      i++
      continue
    }
    if (c === '`') return i + 1
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
 * Unit of count is a **line**, not an occurrence: it is stable under rewording,
 * trivial to explain in a review, and the number a person can act on ("this
 * file has 47 of them"). Counting runs instead would double-count
 * 「剩余 ${n} 天」 and make the baseline drift whenever someone edits prose.
 * @param {string} relPath
 * @param {string} text
 * @returns {number[]} sorted, de-duplicated, 1-based line numbers
 */
export function scanSource(relPath, text) {
  const src = String(text)
  const path = String(relPath).replaceAll('\\', '/')
  const lineOf = lineIndexer(src)
  const found = new Set()
  // A string literal may span lines (template literals do, and so do strings
  // with a trailing backslash), so a span contributes every line *inside it*
  // that actually carries Chinese — not just the line it starts on, and not the
  // blank lines in between.
  // `base` is where `code` starts inside `src` — 0 for a .ts file, the offset
  // of the block's body for a .vue one. Offsets are line-mapped against the
  // whole file so the reported line number is the one an editor shows.
  const addSpan = (code, span, base) => {
    if (isDevLog(code, span.start)) return
    const body = code.slice(span.start, span.end)
    body.split('\n').forEach((line, index) => {
      if (CJK.test(line)) found.add(lineOf(base + span.start) + index)
    })
  }

  if (path.endsWith('.vue')) {
    for (const template of topLevelBlocks(src, 'template')) {
      // In a template, Chinese anywhere outside an HTML comment is copy: text
      // nodes and attribute values alike (`label="真实姓名"`,
      // `placeholder="请输入您的学号"`). Tag and attribute *names* cannot hold
      // Han characters, so there is nothing to exclude.
      const stripped = template.body.replace(/<!--[\s\S]*?-->/g, (m) => m.replace(/[^\n]/g, ' '))
      for (const line of cjkLines(stripped)) found.add(lineOf(template.start) + line - 1)
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

  return [...found].sort((a, b) => a - b)
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
 * Per-file line counts, dropping files with nothing left to pay down so the
 * baseline only ever lists real debt.
 * @param {Array<{path: string, text: string}>} files
 * @returns {Record<string, number>}
 */
export function scanTree(files) {
  /** @type {Record<string, number>} */
  const counts = {}
  for (const { path, text } of files) {
    const rel = String(path).replaceAll('\\', '/')
    if (!shouldScan(rel)) continue
    const count = scanSource(rel, text).length
    if (count > 0) counts[rel] = count
  }
  return Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)))
}

/**
 * @param {ReturnType<typeof compare>} result
 * @param {Map<string, Array<{line: number, text: string}>>} samples counted
 *   lines per file, for the regression report. We cannot tell which of a file's
 *   lines are the new ones without a diff, so all of them are shown for a
 *   regressed file — capped, because the point is to show the shape of the
 *   problem, not to reprint the file.
 */
export function formatReport(result, samples = new Map()) {
  const lines = []
  if (result.regressions.length) {
    lines.push('New hardcoded Chinese (this is what the ratchet blocks):')
    for (const { file, base, now } of result.regressions) {
      lines.push(`  ${file}: ${base} -> ${now}`)
      const counted = samples.get(file) ?? []
      for (const s of counted.slice(0, 8)) lines.push(`      ${s.line}: ${s.text}`)
      if (counted.length > 8) lines.push(`      … ${counted.length - 8} more in this file`)
    }
    lines.push('')
    lines.push('Every Chinese string a user can see goes through the catalog:')
    lines.push('  1. add the key to src/i18n/messages/zh-CN/<namespace>.json and en/<namespace>.json')
    lines.push("  2. call it: t('namespace.component.role') in script, {{ t('…') }} or :label=\"t('…')\" in a template")
    lines.push('Key naming and namespaces: docs/i18n.md. English for platform terms: docs/i18n-glossary.md.')
    lines.push('Comments, console/logger arguments and *.spec.ts files are not counted.')
    lines.push('A string that is not copy at all (a regex over Chinese input, sample data) may stay:')
    lines.push('raise that file in i18n-cjk-baseline.json by hand in the same PR and say why in')
    lines.push('its description. `lint:i18n:update` never raises a count, so that edit is always visible.')
  }
  if (result.improvements.length) {
    lines.push(result.regressions.length ? '' : 'Hardcoded Chinese went down — optionally tighten the baseline:')
    if (result.regressions.length) lines.push('Also improved:')
    for (const { file, base, now } of result.improvements) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('Run: pnpm run lint:i18n:update  (then commit i18n-cjk-baseline.json)')
  }
  lines.push(`total: ${result.currentTotal} line(s) of hardcoded Chinese, baseline allows ${result.baselineTotal}`)
  return lines.join('\n')
}
