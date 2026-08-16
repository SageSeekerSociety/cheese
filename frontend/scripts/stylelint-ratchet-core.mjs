// Pure logic for the design-token ratchet. No I/O, no process — so it can be
// tested directly (see stylelint-ratchet-core.test.mjs, run by `node --test`).
//
// WHY A RATCHET: docs/design-system.md forbids hardcoded colours and off-ladder
// radii, and the tree violates that in 537 places across ~120 files. A gate that
// fails on all of them from day one does not get the backlog fixed; it gets the
// gate deleted. Freezing today's count per file and blocking only NEW violations
// makes the rule enforceable immediately and lets the backlog be paid down file
// by file. Exactly the reasoning behind tsc-baseline.json.
//
// WHY ONLY FOUR RULES: stylelint reports 3379 warnings on this tree, but 2842 of
// them are things like property ORDER and class-name casing — rules that have
// been configured for a long time and never actually run (stylelint appears in
// no workflow and in no Taskfile target). Ratcheting those too would mean an
// unrelated PR gets blocked for touching a file whose declarations happen to be
// in the wrong order, which is how a gate loses its welcome. This ratchet is
// scoped to the DESIGN SYSTEM rules; widening it later is a one-line change to
// DESIGN_RULES below, and should be a deliberate decision with its own baseline
// regeneration, not a side effect.

// `compare` and `tightenedBaseline` operate on plain {file: count} maps and
// carry the subtle parts (a file missing from the baseline has base 0; an
// --update can never LOOSEN a count, so it cannot be used to launder in a
// regression). Importing them keeps the two ratchets provably identical in
// behaviour instead of similar-looking. They are not tsc-specific despite the
// module name.
export { compare, tightenedBaseline } from './tsc-ratchet-core.mjs'

/**
 * The stylelint rules this ratchet governs. Everything else stylelint reports is
 * left ungated — visible when you run stylelint directly, but not a gate.
 */
export const DESIGN_RULES = new Set([
  'color-no-hex',
  'color-named',
  // Numeric rgb()/hsl() literals.
  'declaration-property-value-disallowed-list',
  // Off-ladder border-radius.
  'declaration-property-value-allowed-list',
])

/**
 * Count design-rule violations per file in stylelint's JSON report.
 *
 * @param {Array<{source?: string, warnings?: Array<{rule?: string}>}>} report
 *   Parsed output of `stylelint --formatter json`.
 * @param {(source: string) => string} toRelativePath
 *   Maps stylelint's absolute `source` to the repo-relative path recorded in the
 *   baseline. Injected rather than computed here so this module stays I/O-free
 *   and the baseline stays portable across checkout locations.
 * @returns {Record<string, number>}
 */
export function parseStylelintReport(report, toRelativePath) {
  /** @type {Record<string, number>} */
  const counts = {}
  for (const entry of report ?? []) {
    if (!entry?.source) continue
    const file = toRelativePath(entry.source).replaceAll('\\', '/')
    for (const warning of entry.warnings ?? []) {
      if (!DESIGN_RULES.has(warning?.rule)) continue
      counts[file] = (counts[file] ?? 0) + 1
    }
  }
  return counts
}

/**
 * Human-readable detail for the regressions, so the failure names the offending
 * lines instead of only a count. A ratchet that says "3 -> 4" and nothing else
 * makes people go looking with grep, and grep finds the 235 pre-existing ones
 * too.
 *
 * @param {Array<{source?: string, warnings?: Array<Record<string, unknown>>}>} report
 * @param {(source: string) => string} toRelativePath
 * @param {Set<string>} files Only these (the regressed ones) are detailed.
 */
export function violationDetails(report, toRelativePath, files) {
  const lines = []
  for (const entry of report ?? []) {
    if (!entry?.source) continue
    const file = toRelativePath(entry.source).replaceAll('\\', '/')
    if (!files.has(file)) continue
    for (const warning of entry.warnings ?? []) {
      if (!DESIGN_RULES.has(warning?.rule)) continue
      lines.push(`  ${file}:${warning.line}:${warning.column}  ${warning.text}`)
    }
  }
  return lines
}

/**
 * @param {{regressions: Array<{file: string, base: number, now: number}>,
 *          improvements: Array<{file: string, base: number, now: number}>,
 *          baselineTotal: number, currentTotal: number, ok: boolean}} result
 * @param {string[]} [details]
 */
export function formatReport(result, details = []) {
  const lines = []
  if (result.regressions.length) {
    lines.push('New design-token violations (this is what the ratchet blocks):')
    for (const { file, base, now } of result.regressions) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    if (details.length) {
      lines.push('')
      lines.push('In those files:')
      lines.push(...details)
    }
    lines.push('')
    lines.push('Use a design token instead of a literal colour, and keep radii on the')
    lines.push('6 / 8 / 12 / 999 ladder. See docs/design-system.md.')
    lines.push('')
    lines.push('If you only MOVED existing violations around inside a file, fix them')
    lines.push('rather than re-freezing them: the baseline never rises.')
  }
  if (result.improvements.length) {
    lines.push(result.regressions.length ? '' : 'Violations went down — tighten the baseline:')
    if (result.regressions.length) lines.push('Also improved:')
    for (const { file, base, now } of result.improvements) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('Run: pnpm run lint:style:update  (then commit stylelint-baseline.json)')
  }
  lines.push(`total: ${result.currentTotal} design-token violation(s), baseline allows ${result.baselineTotal}`)
  return lines.join('\n')
}
