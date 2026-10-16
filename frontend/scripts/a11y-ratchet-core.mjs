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

import { parseEslintJsonFor } from './eslint-ratchet-json.mjs'

/** Every rule this ratchet counts carries this prefix; nothing else is counted. */
export const A11Y_RULE_PREFIX = 'vuejs-accessibility/'

/** What is scanned, relative to the frontend root. The rules only read templates. */
export const A11Y_TARGETS = ['src/**/*.vue']

/**
 * Count accessibility violations per file in eslint's `--format json` output —
 * this ratchet's share of it, which is every rule under `A11Y_RULE_PREFIX`. The
 * parsing, the relative-path keys and the "could not judge" set are the same
 * question every rule-counting ratchet asks, so they live in
 * `eslint-ratchet-json.mjs`; this binds it to the plugin prefix.
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
  return parseEslintJsonFor(text, root, (ruleId) => ruleId.startsWith(A11Y_RULE_PREFIX))
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
import { compare, tightenedBaseline } from './tsc-ratchet-core.mjs'

export { compare, tightenedBaseline }

const sum = (counts) => Object.values(counts).reduce((a, b) => a + b, 0)

/**
 * What a baseline key looks like: `parseEslintJson` writes paths relative to the
 * frontend root, and the rules only read `.vue` templates. A move may not invent
 * a key outside that shape — a typo'd path would otherwise sit in the baseline
 * forever, matching no file, and quietly widen the gate.
 */
export const BASELINE_KEY = /^src\/.+\.vue$/

/**
 * Re-key part of the frozen allowance from one file to another: the one edit a
 * split needs, which neither `--update` nor `--freeze-new` can express.
 *
 * Splitting `src/views/A.vue` into itself plus `src/components/B.vue` moves code,
 * and the violations in it, from one baseline key to another. The total does not
 * change, so the per-file ratchet sees `src/components/B.vue: 0 -> 3` and fails:
 * the code moved, the allowance did not. `--update` refuses (it only lowers, and
 * a key that was not there before is a raise); `--freeze-new` accepts, but it
 * accepts the whole tree at once, and a split is exactly when a violation that
 * did not move is hardest to spot among the ones that did.
 *
 * So the caller names the file that gave, the file that took, and how many
 * violations travelled. The count is stated rather than inferred because only
 * the person who ran the split knows it: a violation sitting in the new file
 * looks the same whether it moved there or was written there. Everything that
 * can be checked against the run, is:
 *
 *   - the source froze that many, or the total would have to rise — a move may
 *     only re-key debt that is already on the books;
 *   - the source kept enough for what the tree still shows in it, so a count
 *     larger than what actually left is refused, not frozen;
 *   - the target's new allowance is exactly what the tree measures there: the
 *     move adds allowance, and the only amounts it may add are measured ones;
 *   - the frozen total is the same before and after.
 *
 * A file that only improved is not what this is for — that is `--update`.
 *
 * @param {Record<string, number>} baseline frozen counts, relative keys
 * @param {Record<string, number>} current what this run measured, same keys
 * @param {{from: string, to: string, count: number}} move
 * @returns {{
 *   next: Record<string, number>,
 *   total: number,
 *   source: {file: string, frozen: number, after: number, measured: number},
 *   target: {file: string, frozen: number, after: number, measured: number},
 * }}
 */
export function movedBaseline(baseline, current, move) {
  const { from, to, count } = move
  if (!Number.isInteger(count) || count <= 0) {
    throw new Error(`the count has to be a positive whole number, not ${JSON.stringify(count)}`)
  }
  if (from === to) throw new Error(`the source and the target are the same file (${from})`)
  for (const [flag, file] of [
    ['--move-from', from],
    ['--move-to', to],
  ]) {
    if (!BASELINE_KEY.test(String(file))) {
      throw new Error(`${flag} ${JSON.stringify(file)} is not a baseline key (src/**/*.vue)`)
    }
  }

  const frozen = baseline[from] ?? 0
  const before = sum(baseline)
  if (frozen < count) {
    throw new Error(
      `the baseline freezes ${frozen} violation(s) in ${from}, so ${count} cannot come out of it: the ` +
        `missing ${count - frozen} would be debt nothing paid for, and the frozen total would rise from ` +
        `${before} to ${before + count - frozen}`
    )
  }

  const targetFrozen = baseline[to] ?? 0
  const next = { ...baseline }
  if (frozen === count) delete next[from]
  else next[from] = frozen - count
  next[to] = targetFrozen + count

  const after = sum(next)
  if (after !== before) {
    // Unreachable while the checks above hold, and that is the point: this mode
    // is named for a total that does not move, so if the arithmetic ever stops
    // holding it, refuse to write rather than freeze the difference.
    throw new Error(`the move would change the frozen total from ${before} to ${after}`)
  }

  const leftBehind = current[from] ?? 0
  if (leftBehind > next[from]) {
    throw new Error(
      `the tree still shows ${leftBehind} violation(s) in ${from}, more than the ${next[from]} the move ` +
        `would allow it: fewer violations left ${from} than the count says, and the difference would sit ` +
        `frozen on ${to}`
    )
  }

  const measured = current[to] ?? 0
  if (measured !== next[to]) {
    throw new Error(
      `the tree measures ${measured} violation(s) in ${to}, but the move would freeze ${next[to]} there: ` +
        `a move may only re-key violations that are there, so either the count is wrong (it is the number ` +
        `that travelled) or those violations did not all land in ${to}`
    )
  }

  return {
    next,
    count,
    total: before,
    source: { file: from, frozen, after: frozen - count, measured: leftBehind },
    target: { file: to, frozen: targetFrozen, after: next[to], measured },
  }
}

/**
 * The human half of a move: what it re-keyed, what the tree measured on both
 * sides of it, and which rules the target now carries — the last one is the
 * evidence a reviewer checks the move against ("3 moved, same rule and same
 * element"), and the count alone would not show it.
 *
 * @param {ReturnType<typeof movedBaseline>} plan
 * @param {Array<{file: string, rule: string}>} [violations] every violation this run found
 */
export function formatMove(plan, violations = []) {
  const { source, target, total, count } = plan
  const lines = [`moved ${count} violation(s): ${source.file} -> ${target.file}`]
  lines.push(`  ${source.file}: frozen ${source.frozen} -> ${source.after}, the tree measures ${source.measured}`)
  lines.push(`  ${target.file}: frozen ${target.frozen} -> ${target.after}, the tree measures ${target.measured}`)
  const byRule = countsByRule(violations.filter((v) => v.file === target.file))
  const rules = Object.entries(byRule)
    .map(([rule, n]) => `${rule.slice(rule.indexOf('/') + 1)} ${n}`)
    .join(', ')
  if (rules) lines.push(`  now measured in ${target.file} (check these are what moved): ${rules}`)
  lines.push(`frozen total: ${total} -> ${total}`)
  return lines.join('\n')
}

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
