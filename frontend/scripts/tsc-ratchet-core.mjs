// Pure logic for the vue-tsc ratchet. No I/O, no process — so it can be tested
// directly (see tsc-ratchet-core.test.mjs, run by `node --test` in CI).
//
// WHY A RATCHET AND NOT A PLAIN GATE: the frontend had no type check in CI at
// all, so `vue-tsc --noEmit` starts at 33 errors across 10 files. Blocking on
// zero would mean either landing a large unrelated cleanup first or leaving the
// gate off for weeks. A ratchet blocks the thing that actually matters from day
// one — a NEW error — while the existing ones stay frozen at their current
// count and can be paid down file by file.

const ERROR_LINE = /^(\S[^(]*)\((\d+),(\d+)\): error (TS\d+):/

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

/** @param {ReturnType<typeof compare>} result */
export function formatReport(result) {
  const lines = []
  if (result.regressions.length) {
    lines.push('New type errors (this is what the ratchet blocks):')
    for (const { file, base, now } of result.regressions) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('Fix them, or — if you genuinely reduced errors elsewhere and this')
    lines.push('file is unrelated — check whether you edited a file that was already')
    lines.push('failing. The baseline never rises; that is the point.')
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
