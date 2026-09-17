// Pure logic for the hardcoded-Chinese ratchet. No I/O, no process — so it can
// be tested directly (see i18n-cjk-ratchet-core.test.mjs, run by `node --test`
// in CI). Structure deliberately mirrors tsc-ratchet-core.mjs.
//
// WHY THIS EXISTS, AND WHY IT IS NOT catalog.spec.ts:
// `catalog.spec.ts` checks the catalog against itself — every `zh-CN` key has an
// `en` one or sits in `untranslated.json`, every key has a call site, no English
// value contains Chinese. All of that is scoped to keys that were *already
// extracted*. A Chinese string typed straight into a template is invisible to
// every one of those checks, so the suite can be fully green while the English
// UI is still Chinese. Measured on the tree that introduced this file: 1448
// lines inside `<template>` blocks, across 149 files — including
// `views/user/privacy/DataSharing.vue`, which has zero `t()` calls and no
// catalog keys at all, so nothing above it ever looks at that page.
//
// WHY A RATCHET AND NOT A PLAIN GATE: 2645 hardcoded lines is not a cleanup
// anybody lands in one PR, and a rule that goes red everywhere on day one gets
// switched off rather than fixed (the same reasoning as tsc-baseline.json and
// stylelint-baseline.json). A ratchet blocks the thing that actually matters
// from the first commit — a NEW Chinese literal — while the existing ones stay
// frozen per file and can be paid down file by file.

/**
 * The same character class `catalog.spec.ts` uses for "this English value still
 * has Chinese in it" (U+3400–4DBF, U+4E00–9FFF, U+F900–FAFF). Deliberately one
 * definition of "Chinese" across both gates, so a string can never be Chinese
 * to one and not the other.
 *
 * Note it excludes CJK punctuation (U+3000–303F, U+FF00–FFEF). That is fine
 * here as everywhere: every real Chinese sentence carries a Han character, and
 * a string made of nothing but 「——」 is not text anyone translates.
 */
export const CJK = /[㐀-䶿一-鿿豈-﫿]/

/**
 * A line carrying this token is exempt, and so is the line after it.
 *
 * The alternative to an escape hatch is not "no exemptions", it is people
 * reaching for `--update` — which launders the whole file's real debt along
 * with the one line they meant to allow. Rare but legitimate: a fixture, a
 * regex, a value that is data rather than UI copy. The token lives here as a
 * bare string rather than a comment so it survives into the raw source the
 * scanner reads.
 */
export const ALLOW = 'i18n-cjk-allow'

const ALLOW_NEXT = `${ALLOW}-next-line`

/** Paths the ratchet never looks at. */
export function shouldScan(relPath) {
  const p = String(relPath).replaceAll('\\', '/')
  if (!/\.(vue|ts|js)$/.test(p)) return false
  // `src/i18n/` is the catalog itself: `messages/zh-CN/**` is Chinese by
  // definition, and `languages.ts` holds 「中文」/「English」 on purpose — the
  // language names must read the same under every locale so someone who cannot
  // read the current one can still find their way back (docs/i18n.md §5).
  if (p.startsWith('src/i18n/')) return false
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
    const template = extractBlock(src, 'template')
    if (template) {
      // In a template, Chinese anywhere outside an HTML comment is copy: text
      // nodes and attribute values alike (`label="真实姓名"`,
      // `placeholder="请输入您的学号"`). Tag and attribute *names* cannot hold
      // Han characters, so there is nothing to exclude.
      const stripped = template.body.replace(/<!--[\s\S]*?-->/g, (m) => m.replace(/[^\n]/g, ' '))
      for (const line of cjkLines(stripped)) found.add(lineOf(template.start) + line - 1)
    }
    const script = extractBlock(src, 'script')
    if (script) {
      for (const span of stringSpans(script.body)) addSpan(script.body, span, script.start)
    }
    // `<style>` is deliberately not scanned: the catalog cannot reach CSS, and
    // the only Chinese that ever appears there is a `content:` string, which is
    // a different (and currently non-existent) problem.
  } else {
    for (const span of stringSpans(src)) addSpan(src, span, 0)
  }

  const allowed = allowedLines(src)
  return [...found].filter((line) => !allowed.has(line)).sort((a, b) => a - b)
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

/** 1-based lines carrying a bare `i18n-cjk-allow` token, or the one after it. */
function allowedLines(src) {
  const out = new Set()
  const lines = src.split('\n')
  lines.forEach((line, index) => {
    const lineNo = index + 1
    if (!line.includes(ALLOW)) return
    if (line.includes(ALLOW_NEXT)) out.add(lineNo + 1)
    else out.add(lineNo)
  })
  return out
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
 * `<template>…</template>` / `<script …>…</script>` block and its content
 * offset. The template match is greedy so the *outermost* block wins — inner
 * `<template #slot>` elements are part of it, which is what we want.
 */
function extractBlock(src, tag) {
  const open = src.search(new RegExp(`<${tag}\\b`))
  if (open === -1) return null
  const openEnd = src.indexOf('>', open)
  if (openEnd === -1) return null
  const close = tag === 'template' ? src.lastIndexOf('</template>') : src.indexOf(`</${tag}>`, openEnd)
  if (close === -1 || close < openEnd) return null
  // `.vue` blocks are wrapped in a newline; dropping it keeps line numbers
  // right for every line after the first one in the block.
  let start = openEnd + 1
  if (src[start] === '\n') start++
  return { start, body: src.slice(start, close) }
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
 * Compare a run against the baseline.
 *
 * A file over its baseline is a regression; a file under it is an improvement
 * the baseline should be tightened to. A file absent from the baseline with any
 * Chinese is a regression with base 0 — the common case this guard exists for,
 * a new component written in Chinese that never went through `t()`.
 * @param {Record<string, number>} baseline
 * @param {Record<string, number>} current
 */
export function compare(baseline, current) {
  const regressions = []
  const improvements = []
  for (const file of Object.keys(current).sort()) {
    const base = baseline[file] ?? 0
    const now = current[file]
    if (now > base) regressions.push({ file, base, now })
  }
  for (const file of Object.keys(baseline).sort()) {
    const base = baseline[file]
    const now = current[file] ?? 0
    if (now < base) improvements.push({ file, base, now })
  }
  const total = (counts) => Object.values(counts).reduce((a, b) => a + b, 0)
  return {
    regressions,
    improvements,
    baselineTotal: total(baseline),
    currentTotal: total(current),
    ok: regressions.length === 0,
  }
}

/**
 * The baseline the repo should now record — never looser than reality, so an
 * `--update` after a genuine regression cannot launder it in.
 * @param {Record<string, number>} baseline
 * @param {Record<string, number>} current
 */
export function tightenedBaseline(baseline, current) {
  /** @type {Record<string, number>} */
  const next = {}
  for (const file of new Set([...Object.keys(baseline), ...Object.keys(current)])) {
    const value = Math.min(baseline[file] ?? Infinity, current[file] ?? 0)
    if (value > 0) next[file] = value
  }
  return Object.fromEntries(Object.entries(next).sort(([a], [b]) => a.localeCompare(b)))
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
    lines.push("Move the string into the catalog and call it: t('namespace.section.key').")
    lines.push('Layout: docs/i18n.md. Wording of platform concepts: docs/i18n-glossary.md.')
    lines.push('A string that genuinely must stay Chinese — a fixture, a regex, a value')
    lines.push(`that is data rather than copy — gets a \`${ALLOW_NEXT}\` comment on the`)
    lines.push('line before it. The baseline itself never rises; that is the point.')
  }
  if (result.improvements.length) {
    lines.push(result.regressions.length ? '' : 'Hardcoded Chinese went down — tighten the baseline:')
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
