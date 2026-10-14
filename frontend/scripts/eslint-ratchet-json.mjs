// Turning an eslint `--format json` run into per-file counts, for every ratchet
// that counts one rule's messages. Shared rather than copied: the "unjudged"
// rule below (a file eslint could not parse is not a file with zero violations)
// is the difference between a gate and a gate that goes green over a file it
// never read, and a second copy of it is a second answer to that question.
//
// The caller names the rule id it counts; the file keys are made relative to
// `root`, because eslint reports absolute paths and a baseline keyed by those
// would only match on the machine that wrote it — not in CI, not in a second
// worktree.
/**
 * @param {string} text raw eslint JSON output
 * @param {string} root absolute path the file names are made relative to
 * @param {string | ((ruleId: string) => boolean)} rule the one rule whose
 *   messages are counted — its exact id, or a predicate for a ratchet that
 *   spans a family of rules (the a11y gate counts a whole plugin prefix).
 * @returns {{ counts: Record<string, number>, violations: {file: string, line: number, column: number, rule: string, message: string}[], unjudged: string[] }}
 */
export function parseEslintJsonFor(text, root, rule) {
  const matches = typeof rule === 'function' ? rule : (id) => id === rule
  const parsed = JSON.parse(String(text))
  if (!Array.isArray(parsed)) {
    throw new TypeError('eslint output is not a JSON array of file results')
  }

  const prefix = String(root).replaceAll('\\', '/').replace(/\/$/, '') + '/'
  /** @type {Record<string, number>} */
  const counts = {}
  /** @type {{file: string, line: number, column: number, rule: string, message: string}[]} */
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
    const hits = messages.filter((m) => matches(String(m.ruleId)))
    for (const m of hits) {
      violations.push({ file, line: m.line, column: m.column, rule: m.ruleId, message: m.message })
    }
    if (hits.length > 0) counts[file] = (counts[file] ?? 0) + hits.length
  }
  return { counts, violations, unjudged }
}
