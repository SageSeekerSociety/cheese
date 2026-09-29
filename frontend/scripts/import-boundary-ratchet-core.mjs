// The component boundary rule, and the pure logic that counts violations of it.
// No I/O, no eslint import — so the rule can be declared ONCE and read from two
// places: `eslint.boundary.config.mjs` (which turns it into a lint rule) and
// `import-boundary-ratchet.mjs` (which decides whether the counts are allowed).
// A rule id that drifts between the enforcing config and the counting script
// would silently count nothing, which is the failure mode this shape prevents.
//
// THE RULE. A component under `src/components/**` renders what it is given. It
// may not reach for the API layer (`@/api`, `@/network/**`, `@/services/**`)
// and it may not navigate (`vue-router`). The page that owns the component
// fetches, decides and routes; the component takes props and emits events. See
// .claude/rules/architecture.md for why, and for what it costs to break.
//
// `src/views/**` is deliberately unrestricted: a view is where fetching and
// routing are supposed to live, and a rule that forbade them there would just
// be moved around. `src/layouts/**` and the proto/ boards are outside the rule
// for the same reason — they are pages.
//
// Spec files are exempt (see BOUNDARY_IGNORES): a test mounts a router and
// mocks the API on purpose, and forbidding that would only push tests away from
// the components they exercise.

/** The eslint rule that carries the boundary. Must match the id eslint reports. */
export const BOUNDARY_RULE_ID = 'no-restricted-imports'

/** Where the rule applies, relative to the frontend root. */
export const BOUNDARY_FILES = ['src/components/**/*.vue', 'src/components/**/*.ts', 'src/components/**/*.js']

/** Test files inside components/ are not shipped code. */
export const BOUNDARY_IGNORES = [
  '**/*.spec.ts',
  '**/*.spec.js',
  '**/*.spec.vue',
  '**/*.test.ts',
  '**/*.test.js',
  '**/*.test.vue',
]

/** The rule body, passed straight to eslint's `no-restricted-imports`. */
export const boundaryOptions = {
  paths: [
    {
      name: 'vue-router',
      message:
        'A component under src/components must not navigate or read the route. ' +
        'Emit an event (or take a callback prop) and let the view it is rendered ' +
        'from decide where that goes. See .claude/rules/architecture.md.',
    },
  ],
  patterns: [
    {
      group: ['@/api', '@/api/**', '@/network', '@/network/**', '@/services', '@/services/**'],
      message:
        'A component under src/components must not call the API layer. Take the ' +
        'data as a prop and emit the intent; fetch in the view or a composable. ' +
        'See .claude/rules/architecture.md.',
    },
  ],
}

/**
 * Count boundary violations per file in eslint's `--format json` output.
 *
 * Keys are relative to `root` (forward slashes, no leading `./`): eslint reports
 * absolute paths, and a baseline keyed by those would only match on the machine
 * that wrote it — not in CI, not in a second worktree. `root` is a parameter
 * rather than a constant so the unit test can drive it with a fixture.
 *
 * Returns `{ counts, unjudged }`. `unjudged` is non-empty when eslint could not
 * parse a file (`"fatal": true`) or reported a message with no rule id — a
 * syntax error, a missing parser, a plugin that crashed. Those are neither a
 * pass nor a violation of this rule, and counting them as zero is exactly how a
 * boundary check reports a clean tree it never read. The caller must treat a
 * non-empty `unjudged` as "could not judge".
 *
 * @param {string} text raw eslint JSON output
 * @param {string} root absolute path the file names are made relative to
 * @returns {{ counts: Record<string, number>, unjudged: string[] }}
 */
export function parseEslintJson(text, root = process.cwd()) {
  const parsed = JSON.parse(String(text))
  if (!Array.isArray(parsed)) {
    throw new TypeError('eslint output is not a JSON array of file results')
  }

  const prefix = String(root).replaceAll('\\', '/').replace(/\/$/, '') + '/'
  /** @type {Record<string, number>} */
  const counts = {}
  const unjudged = []
  for (const result of parsed) {
    const absolute = String(result.filePath ?? '').replaceAll('\\', '/')
    const file = absolute.startsWith(prefix) ? absolute.slice(prefix.length) : absolute
    const messages = result.messages ?? []
    if (result.fatal === true || messages.some((m) => m.ruleId == null)) {
      unjudged.push(`${file}: ${messages.map((m) => m.message).join('; ') || 'fatal'}`)
      continue
    }
    const hit = messages.filter((m) => m.ruleId === BOUNDARY_RULE_ID).length
    if (hit > 0) counts[file] = (counts[file] ?? 0) + hit
  }
  return { counts, unjudged }
}

// The ratchet arithmetic is the same question the vue-tsc and stylelint
// ratchets ask of their own counts — a file may not go up, a file that went
// down should have its baseline tightened, and a stale baseline entry is not a
// failure. It lives in one place (tsc-ratchet-core.mjs) rather than in three
// copies that can drift; a second definition of "regression" is a second answer
// to what the baseline means.
export { compare, tightenedBaseline } from './tsc-ratchet-core.mjs'

/**
 * The human-readable half: what went wrong, and what to do about it.
 * Takes what `compare()` returns.
 */
export function formatBoundaryReport(result) {
  const lines = []
  if (result.regressions.length) {
    lines.push('New component boundary violations (this is what the ratchet blocks):')
    for (const { file, base, now } of result.regressions) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('A component under src/components must render from props alone: no')
    lines.push('API layer, no vue-router. Move the fetch/navigation up to the view,')
    lines.push('pass the data down, emit the intent up. If the component genuinely')
    lines.push('has to do this, the honest fix is a view or a composable — not a')
    lines.push('line in import-boundary-baseline.json, which only ever goes down.')
  }
  if (result.improvements.length) {
    lines.push(result.regressions.length ? 'Also improved:' : 'Boundary debt went down — tighten the baseline:')
    for (const { file, base, now } of result.improvements) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('Run: pnpm run lint:boundary:update  (then commit import-boundary-baseline.json)')
  }
  lines.push(`total: ${result.currentTotal} violation(s), baseline allows ${result.baselineTotal}`)
  return lines.join('\n')
}
