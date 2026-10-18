// The vue-tsc output parser, and the per-file count arithmetic the remaining
// ratchets share. No I/O, no process — so it can be tested directly (see
// tsc-ratchet-core.test.mjs, run by `node --test` in CI).
//
// The type check itself is a plain gate: any error vue-tsc reports fails it
// (scripts/tsc-ratchet.mjs). `compare` and `tightenedBaseline` below are not
// used by it; they stay here because the stylelint, i18n-comment and
// global-fallback checks import them from this file.

const ERROR_LINE = /^(\S[^(]*)\((\d+),(\d+)\): error (TS\d+):/

/**
 * Every diagnostic, including its indented explanation, so a failure prints
 * the full message without a second type-check run. Only an anchor line
 * (`file(line,col): error TSxxxx:` at column 0) starts one; indented
 * continuation lines belong to the preceding diagnostic.
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
 * The human half: every error vue-tsc reported, at its file(line,col), with its
 * full explanation, so the reader fixes it without a second run.
 * @param {ReturnType<typeof parseTscDiagnostics>} diagnostics
 */
export function formatReport(diagnostics) {
  if (!diagnostics.length) return 'vue-tsc: no type errors'
  const lines = ['Type errors:']
  for (const { file, line, column, code, message } of diagnostics) {
    lines.push(`  ${file}(${line},${column}): error ${code}: ${message}`)
    const name = (code === 'TS2304' || code === 'TS2552') && message.match(/Cannot find name '([^']+)'/)?.[1]
    if (name === 'PropType') lines.push("    For a type-only use: import type { PropType } from 'vue'")
  }
  lines.push('')
  lines.push('Fix each one at that file(line,col). Any error fails the check; there is no')
  lines.push('baseline. To check only the files you changed: task ci:fast (from the repo root).')
  lines.push('Chapter: docs/manual/dev/frontend.md (质量闸 / gates).')
  lines.push(`total: ${diagnostics.length} error(s)`)
  return lines.join('\n')
}
