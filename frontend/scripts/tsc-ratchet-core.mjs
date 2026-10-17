// Pure logic for the vue-tsc ratchet. No I/O, no process — so it can be tested
// directly (see tsc-ratchet-core.test.mjs, run by `node --test` in CI).
//
// WHY A RATCHET AND NOT A PLAIN GATE: the frontend had no type check in CI at
// all, so a bare `vue-tsc --noEmit` starts on the errors the tree already has.
// Blocking on zero would mean either landing a large unrelated cleanup first or
// leaving the gate off for weeks. A ratchet blocks the thing that actually
// matters from day one — a NEW error — while the existing ones stay frozen at
// their current count and can be paid down file by file.

const ERROR_LINE = /^(\S[^(]*)\((\d+),(\d+)\): error (TS\d+):/
const MISSING_NAME_CODES = ['TS2304', 'TS2552']

/**
 * Every diagnostic, including its indented explanation, so a failure prints
 * the full message without a second type-check run. The counting regex is
 * unchanged; continuation lines belong to the preceding diagnostic.
 * @param {string} text
 * @returns {{file: string, line: number, column: number, code: string, message: string}[]}
 */
export function parseTscDiagnostics(text) {
  const diagnostics = []
  let previous
  for (const raw of String(text).split(/\r?\n/)) {
    const m = raw.match(ERROR_LINE)
    if (m) {
      previous = {
        file: m[1].replaceAll('\\', '/').trim(),
        line: Number(m[2]),
        column: Number(m[3]),
        code: m[4],
        message: raw.slice(m[0].length).trim(),
      }
      diagnostics.push(previous)
    } else if (previous && /^[ \t]+\S/.test(raw)) {
      previous.message += `\n${raw}`
    } else {
      previous = undefined
    }
  }
  return diagnostics
}

/**
 * Count `error TSxxxx` diagnostics per file in raw vue-tsc/tsc output.
 * Continuation lines (indented explanation) are ignored — only the anchor line
 * of each diagnostic starts at column 0 with `file(line,col): error TSxxxx:`.
 * @param {string} text
 * @returns {Record<string, number>}
 */
export function parseTscOutput(text) {
  /** @type {Record<string, number>} */
  const counts = {}
  for (const raw of String(text).split(/\r?\n/)) {
    const m = raw.match(ERROR_LINE)
    if (!m) continue
    const file = m[1].replaceAll('\\', '/').trim()
    counts[file] = (counts[file] ?? 0) + 1
  }
  return counts
}

/**
 * Compare a run against the baseline.
 *
 * A file over its baseline is a regression; a file under it is an improvement
 * the baseline should be tightened to. A file absent from the baseline with any
 * error is a regression with base 0 — that is the common case this guard exists
 * for, a brand new error in a file that used to be clean.
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
 * Missing names have their own per-code, per-file budgets. Fixing an assignment
 * error cannot pay for a missing import, even when a split keeps the total flat.
 * Keep `compare` generic: other ratchets use its numeric-map contract too.
 * @param {Record<string, number>} baseline
 * @param {Record<string, number>} current
 * @param {ReturnType<typeof parseTscDiagnostics>} diagnostics
 * @param {Record<string, Record<string, number>>} [missingNames]
 */
export function compareTsc(baseline, current, diagnostics, missingNames = {}) {
  const result = compare(baseline, current)
  const missingNameRegressions = []
  const missingNameImprovements = []
  const nextMissingNames = {}
  for (const code of MISSING_NAME_CODES) {
    const counts = {}
    for (const diagnostic of diagnostics) {
      if (diagnostic.code === code) counts[diagnostic.file] = (counts[diagnostic.file] ?? 0) + 1
    }
    // A file absent from the main baseline has no inherited allowance.
    const frozen = Object.fromEntries(
      Object.entries(missingNames[code] ?? {}).filter(([file]) => Object.hasOwn(baseline, file))
    )
    const delta = compare(frozen, counts)
    missingNameRegressions.push(...delta.regressions.map((entry) => ({ ...entry, code })))
    missingNameImprovements.push(...delta.improvements.map((entry) => ({ ...entry, code })))
    // Only existing allowances may survive an update; never mint a new one.
    nextMissingNames[code] = Object.fromEntries(
      Object.keys(frozen)
        .sort()
        .map((file) => [file, Math.min(frozen[file], counts[file] ?? 0)])
        .filter(([, count]) => count > 0)
    )
  }
  return {
    ...result,
    missingNameRegressions,
    missingNameImprovements,
    nextMissingNames,
    ok: result.ok && missingNameRegressions.length === 0,
  }
}

/**
 * The baseline the repo should now record — never looser than reality, so a
 * `--update` after a genuine regression cannot be used to launder it in.
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
 * The human half: what went wrong, where, and what to do about it.
 * `diagnostics` is `parseTscDiagnostics(output)`; its lines are printed for the
 * files that regressed, including a missing-name budget regression at flat totals.
 * @param {ReturnType<typeof compare> | ReturnType<typeof compareTsc>} result
 * @param {Array<{file: string, line: number, column: number, code: string, message: string}>} [diagnostics]
 */
export function formatReport(result, diagnostics = []) {
  const lines = []
  if (result.regressions.length) {
    lines.push('New type errors (this is what the ratchet blocks):')
    for (const { file, base, now } of result.regressions) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    const regressed = new Set(result.regressions.map((r) => r.file))
    const shown = diagnostics.filter((d) => regressed.has(d.file))
    if (shown.length) {
      lines.push('')
      lines.push('The errors vue-tsc reported, at their file(line,col) (old and new')
      lines.push('alike — the count is per file, so the old ones are listed too):')
      for (const { file, line, column, code, message } of shown) {
        lines.push(`  ${file}(${line},${column}): error ${code}: ${message}`)
      }
    }
    lines.push('')
    lines.push('Fix each one at that file(line,col); its full explanation is above. The')
    lines.push('ratchet blocks only NEW errors and the baseline never rises: if you')
    lines.push('genuinely reduced errors elsewhere and this file is unrelated, check')
    lines.push('whether you edited a file that was already failing.')
    lines.push('Chapter: docs/manual/dev/frontend.md (质量闸 / gates).')
  }
  if (result.missingNameRegressions?.length) {
    lines.push('')
    lines.push('missing type/value import after split — missing names have separate budgets:')
    for (const { file, code, base, now } of result.missingNameRegressions) {
      lines.push(`  ${file} ${code}: ${base} -> ${now}`)
      for (const diagnostic of diagnostics.filter((d) => d.file === file && d.code === code)) {
        const { line, column, message } = diagnostic
        lines.push(`  ${file}(${line},${column}): error ${code}: ${message}`)
        const name = message.match(/Cannot find name '([^']+)'/)?.[1]
        if (name) {
          const source = name === 'PropType' ? 'vue' : '<declaring module>'
          lines.push(`    For a type-only use: import type { ${name} } from '${source}'`)
        }
      }
    }
    lines.push('Use a value import for a runtime value; correct a misspelling instead of importing it.')
    lines.push('Other errors going down cannot pay for a missing name, including in a new split file.')
    lines.push('Run: task ci:fast  (from the repo root; checks only the changed files)')
  }
  if (result.missingNameImprovements?.length) {
    lines.push('')
    lines.push('Missing-name errors went down — tighten their separate baseline:')
    for (const { file, code, base, now } of result.missingNameImprovements) {
      lines.push(`  ${file} ${code}: ${base} -> ${now}`)
    }
    lines.push('Run: pnpm run typecheck:update  (then commit tsc-baseline.json)')
  }
  if (result.improvements.length) {
    lines.push(result.regressions.length ? '' : 'Type errors went down — tighten the baseline:')
    if (result.regressions.length) lines.push('Also improved:')
    for (const { file, base, now } of result.improvements) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('Run: pnpm run typecheck:update  (then commit tsc-baseline.json)')
  }
  lines.push(`total: ${result.currentTotal} error(s), baseline allows ${result.baselineTotal}`)
  return lines.join('\n')
}
