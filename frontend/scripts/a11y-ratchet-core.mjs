// Pure logic for the accessibility ratchet. No I/O, no eslint import — so it can
// be tested directly (see a11y-ratchet.test.mjs, run by `pnpm run test:ratchet`).
//
// WHAT IT CATCHES. eslint-plugin-vuejs-accessibility reads templates, one
// element at a time: a click handler on a <div> with no keyboard equivalent and
// no role, an <input> or <label> that is not tied to a name, an <img> with no
// alt, an aria-* attribute that does not exist, a role missing the attributes
// it requires. What it cannot see is anything that only exists at runtime —
// focus order, whether focus is trapped in a dialog or returned when it closes,
// colour contrast, what a screen reader actually announces. A green run says
// none of the static patterns were added, not that the page is accessible.
//
// WHY A RATCHET. The tree breaks these rules in ~159 places across ~79 files.
// Freezing today's count per file and blocking only NEW violations makes the
// rules enforceable now and lets the backlog be paid down file by file — the
// reasoning behind tsc-baseline.json, stylelint-baseline.json and
// import-boundary-baseline.json, whose shape this copies.
//
// THE KEY IS THE FILE, NOT THE LINE. A baseline keyed by line would fail every
// PR that adds a line above an old violation. Per-file counts let code move
// around inside a file freely; the cost, shared with the other three ratchets,
// is that fixing one violation in a file buys room for one new one there until
// somebody runs the update.

/** Every rule this ratchet counts carries this prefix; nothing else is counted. */
export const A11Y_RULE_PREFIX = 'vuejs-accessibility/'

/** What is scanned, relative to the frontend root. The rules only read templates. */
export const A11Y_TARGETS = ['src/**/*.vue']

/**
 * Read eslint's `--format json` output.
 *
 * Keys are relative to `root` (forward slashes, no leading `./`): eslint reports
 * absolute paths, and a baseline keyed by those would only match on the machine
 * that wrote it.
 *
 * `unjudged` is non-empty when eslint could not parse a file (`"fatal": true`)
 * or reported a message with no rule id — a syntax error, a missing parser, a
 * plugin that threw. Counting those as zero is how a gate goes green over files
 * it never read, so the caller treats a non-empty `unjudged` as "could not
 * judge".
 *
 * @param {string} text raw eslint JSON output
 * @param {string} root absolute path the file names are made relative to
 * @returns {{
 *   counts: Record<string, number>,
 *   violations: Array<{file: string, line: number, column: number, rule: string, message: string}>,
 *   unjudged: string[],
 * }}
 */
export function parseEslintJson(text, root) {
  const parsed = JSON.parse(String(text))
  if (!Array.isArray(parsed)) {
    throw new TypeError('eslint output is not a JSON array of file results')
  }

  const prefix = String(root).replaceAll('\\', '/').replace(/\/$/, '') + '/'
  /** @type {Record<string, number>} */
  const counts = {}
  const violations = []
  const unjudged = []
  for (const result of parsed) {
    const absolute = String(result.filePath ?? '').replaceAll('\\', '/')
    const file = absolute.startsWith(prefix) ? absolute.slice(prefix.length) : absolute
    const messages = result.messages ?? []
    if (result.fatal === true || messages.some((m) => m.ruleId == null)) {
      unjudged.push(`${file}: ${messages.map((m) => m.message).join('; ') || 'fatal'}`)
      continue
    }
    for (const m of messages) {
      if (!String(m.ruleId).startsWith(A11Y_RULE_PREFIX)) continue
      counts[file] = (counts[file] ?? 0) + 1
      violations.push({ file, line: m.line, column: m.column, rule: m.ruleId, message: m.message })
    }
  }
  return { counts, violations, unjudged }
}

/** Violation counts per rule, for the summary line and the PR that changes them. */
export function countsByRule(violations) {
  /** @type {Record<string, number>} */
  const byRule = {}
  for (const { rule } of violations) byRule[rule] = (byRule[rule] ?? 0) + 1
  return Object.fromEntries(Object.entries(byRule).sort(([, a], [, b]) => b - a))
}

// The ratchet arithmetic is the one the vue-tsc, stylelint and boundary
// ratchets use: a file may not go up, an absent file starts at 0, and --update
// can never loosen a count, so it cannot be used to launder a regression in.
export { compare, tightenedBaseline } from './tsc-ratchet-core.mjs'

/**
 * The human-readable half. Takes what `compare()` returns and the violations in
 * the files that regressed, so the failure names the line and the rule instead
 * of only a count — "3 -> 4" alone sends people to grep, and grep finds the
 * three old ones too.
 *
 * @param {ReturnType<typeof import('./tsc-ratchet-core.mjs').compare>} result
 * @param {Array<{file: string, line: number, column: number, rule: string, message: string}>} [details]
 */
export function formatA11yReport(result, details = []) {
  const lines = []
  if (result.regressions.length) {
    lines.push('New accessibility violations (this is what the ratchet blocks):')
    for (const { file, base, now } of result.regressions) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    if (details.length) {
      lines.push('')
      lines.push('In those files (old and new alike — the count is per file):')
      for (const { file, line, column, rule, message } of details) {
        lines.push(`  ${file}:${line}:${column}  ${rule}  ${message}`)
      }
    }
    lines.push('')
    lines.push('Give the element a keyboard path and a name: a <button> (or BaseButton)')
    lines.push('instead of a clickable <div>, a label tied to its control. Where the')
    lines.push('pattern is deliberate, an eslint-disable-next-line comment naming the')
    lines.push('reason is reviewable; a higher number in a11y-baseline.json is not —')
    lines.push('the baseline only goes down.')
  }
  if (result.improvements.length) {
    lines.push(result.regressions.length ? '' : 'Accessibility violations went down — tighten the baseline:')
    if (result.regressions.length) lines.push('Also improved:')
    for (const { file, base, now } of result.improvements) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('Run: pnpm run lint:a11y:update  (then commit a11y-baseline.json)')
  }
  lines.push(`total: ${result.currentTotal} accessibility violation(s), baseline allows ${result.baselineTotal}`)
  return lines.join('\n')
}
