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
 * The anchor line of every diagnostic, kept whole (`{ file, line, column, code,
 * message }`), so a failure can print the `file(line,col): error TSxxxx:` line
 * vue-tsc printed instead of a bare count. The message is whatever follows the
 * prefix `ERROR_LINE` already matches — the counting regex is unchanged.
 * @param {string} text
 * @returns {{file: string, line: number, column: number, code: string, message: string}[]}
 */
export function parseTscDiagnostics(text) {
  const diagnostics = []
  for (const raw of String(text).split(/\r?\n/)) {
    const m = raw.match(ERROR_LINE)
    if (!m) continue
    diagnostics.push({
      file: m[1].replaceAll('\\', '/').trim(),
      line: Number(m[2]),
      column: Number(m[3]),
      code: m[4],
      message: raw.slice(m[0].length).trim(),
    })
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
 * files that regressed. The per-file count is still what decides (see `compare`).
 * @param {ReturnType<typeof compare>} result
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
    lines.push('Fix each one at that file(line,col). `pnpm run typecheck:raw` (vue-tsc')
    lines.push('--noEmit) reprints the same diagnostic with its full explanation. The')
    lines.push('ratchet blocks only NEW errors and the baseline never rises: if you')
    lines.push('genuinely reduced errors elsewhere and this file is unrelated, check')
    lines.push('whether you edited a file that was already failing.')
    lines.push('Chapter: docs/manual/dev/frontend.md (质量闸 / gates).')
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
