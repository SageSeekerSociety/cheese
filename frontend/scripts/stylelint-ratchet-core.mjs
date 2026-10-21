// Design-token report parsing and presentation. Pure style findings remain ungated.

/**
 * The stylelint rules that gate the tree. Everything else stylelint reports is
 * left ungated — visible when you run stylelint directly, but not a gate.
 */
export const DESIGN_RULES = new Set([
  'color-no-hex',
  'color-named',
  // Numeric rgb()/hsl() literals.
  'declaration-property-value-disallowed-list',
])

/**
 * Count design-rule violations per file in stylelint's JSON report.
 *
 * @param {Array<{source?: string, warnings?: Array<{rule?: string}>}>} report
 *   Parsed output of `stylelint --formatter json`.
 * @param {(source: string) => string} toRelativePath
 *   Maps stylelint's absolute `source` to the repo-relative path recorded in the
 *   report. Injected rather than computed here so this module stays I/O-free
 *   and paths stay portable across checkout locations.
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
 * Files stylelint could not parse, with what it choked on.
 *
 * A parse error is not a design violation, so `parseStylelintReport` drops it —
 * and a file it could not read contributes zero violations, which reads to the
 * check as a clean file. That is how an unclosed block once shipped past a
 * green check: the suite does not compile CSS, the type checker does not read
 * it, and the one tool that does was counting the file as clean.
 *
 * @param {Array<{source?: string, warnings?: Array<{rule?: string, line?: number, text?: string}>}>} report
 * @param {(source: string) => string} toRelativePath
 * @returns {string[]} One line per broken file; empty when every file parsed.
 */
export function syntaxErrors(report, toRelativePath) {
  const lines = []
  for (const entry of report ?? []) {
    if (!entry?.source) continue
    for (const warning of entry.warnings ?? []) {
      if (warning?.rule !== 'CssSyntaxError') continue
      const file = toRelativePath(entry.source).replaceAll('\\', '/')
      lines.push(`  ${file}:${warning.line ?? '?'} ${warning.text ?? 'CSS syntax error'}`)
    }
  }
  return lines
}

/**
 * Human-readable details name the offending lines as well as their count.
 *
 * @param {Array<{source?: string, warnings?: Array<Record<string, unknown>>}>} report
 * @param {(source: string) => string} toRelativePath
 * @param {Set<string>} files Only these files are detailed.
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

/** Human-readable findings for the zero-tolerance design-token check. */
export function formatReport(result, details = []) {
  const lines = []
  if (!result.ok) {
    lines.push('Design-token violations:', ...details, '')
    lines.push('Use a design token instead of a literal colour. See docs/design-system.md.')
  }
  lines.push(`total: ${result.currentTotal} design-token violation(s)`)
  return lines.join('\n')
}
