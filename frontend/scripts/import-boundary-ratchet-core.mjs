// The component boundary rule, and the pure logic that counts violations of it.
// No I/O, no eslint import — so the rule can be declared ONCE and read from two
// places: `eslint.boundary.config.mjs` (which turns it into a lint rule) and
// `import-boundary-ratchet.mjs` (which decides whether the counts are allowed).
// A rule id that drifts between the enforcing config and the counting script
// would silently count nothing, which is the failure mode this shape prevents.
//
// THE RULE. A component under `src/components/**` renders what it is given. It
// may not reach for the API layer (`src/api.ts`, `src/network/**`,
// `src/services/**`) and it may not navigate (`vue-router`). The page that owns
// the component fetches, decides and routes; the component takes props and
// emits events. See .claude/rules/architecture.md for why, and for what it
// costs to break.
//
// It is judged by WHERE THE IMPORT RESOLVES, not by how the specifier is
// spelled. `@/api`, `../api` and `../../api` are three spellings of one
// dependency and all three are the same violation; a path that only looks like
// the API layer (`src/components/chat/services/*`, reached as `./services/x`)
// is not one. This is a local rule rather than eslint's `no-restricted-imports`
// for exactly that reason: `no-restricted-imports` matches the specifier as a
// glob, so the alias forms were counted and the relative ones — 30+ components
// reaching src/api.ts the long way — were invisible.
//
// `src/views/**` is deliberately unrestricted: a view is where fetching and
// routing are supposed to live, and a rule that forbade them there would just
// be moved around. `src/layouts/**` and the proto/ boards are outside the rule
// for the same reason — they are pages.
//
// Spec files are exempt (see BOUNDARY_IGNORES): a test mounts a router and
// mocks the API on purpose, and forbidding that would only push tests away from
// the components they exercise.

import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

/** The eslint rule that carries the boundary. Must match the id eslint reports. */
export const BOUNDARY_RULE_ID = 'boundary/no-api-or-router'

/** The plugin name half of that id; a flat config registers `boundaryPlugin` under it. */
export const BOUNDARY_PLUGIN_NAME = BOUNDARY_RULE_ID.slice(0, BOUNDARY_RULE_ID.indexOf('/'))

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

// The frontend root is derived from this file's own location (scripts/…), not
// from `process.cwd()`: the ratchet, the editor and a `pnpm --dir frontend`
// invocation all run from different directories, and a rule that resolved the
// `@` alias against the wrong one would quietly judge nothing.
const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC_DIR = join(FRONTEND_ROOT, 'src')

/** The rule body, passed to the rule by `eslint.boundary.config.mjs`. */
export const boundaryOptions = {
  /** First path segment under src/ that a component may not import from. */
  apiLayer: ['api', 'network', 'services'],
  /** Packages a component may not import, however it reaches them. */
  routerModules: ['vue-router'],
  messages: {
    api:
      'A component under src/components must not call the API layer. Take the ' +
      'data as a prop and emit the intent; fetch in the view or a composable. ' +
      'See .claude/rules/architecture.md.',
    router:
      'A component under src/components must not navigate or read the route. ' +
      'Emit an event (or take a callback prop) and let the view it is rendered ' +
      'from decide where that goes. See .claude/rules/architecture.md.',
  },
}

/**
 * Resolve an import specifier to an absolute path, or `null` when it names a
 * bare package (`vue-router`, `lodash-es`) rather than something in the tree.
 *
 * The three forms that reach the same file are all handled here: the `@` alias
 * (vite.config.ts maps it to src/, tsconfig.json to `src/*`), a relative path,
 * and a root-relative `/src/…`. Nothing is resolved through node_modules — a
 * bare specifier is a package, and `routerModules` is matched by name.
 */
function resolveImport(specifier, filename) {
  if (specifier.startsWith('@/')) return join(SRC_DIR, specifier.slice(2))
  if (specifier.startsWith('.')) return resolve(dirname(filename), specifier)
  if (specifier.startsWith('/src/')) return join(FRONTEND_ROOT, specifier.slice(1))
  if (specifier.startsWith('src/')) return join(FRONTEND_ROOT, specifier)
  return null
}

/**
 * The `src`-relative module path of a resolved file — extension stripped,
 * trailing `/index` stripped — or `null` when it is not under src/ at all.
 * `src/api.ts` and `src/api/index.ts` both come back as `api`, which is what
 * the rule compares against, so the file-vs-directory spelling of the API
 * layer does not decide whether it is caught.
 */
function srcModulePath(resolved) {
  const rel = relative(SRC_DIR, resolved)
  if (rel === '' || rel === '..' || rel.startsWith(`..${sep}`) || isAbsolute(rel)) return null
  return rel
    .split(sep)
    .join('/')
    .replace(/\.(ts|tsx|js|jsx|mjs|cjs|vue|json)$/, '')
    .replace(/\/index$/, '')
}

/** The rule. One rule id, so the config and the counting script cannot drift. */
export const boundaryRule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'A component under src/components must not import the API layer or vue-router, judged by where the import resolves rather than how it is spelled.',
    },
    schema: [{ type: 'object' }],
  },
  create(context) {
    const options = { ...boundaryOptions, ...(context.options[0] ?? {}) }
    const filename = String(context.filename ?? context.getFilename())

    /**
     * A declaration that crosses the API-layer line in name only: `import type
     * { X } from '@/network/api/x/types'`, `export type { X } from …`, or one
     * whose every specifier is marked `type`.
     *
     * A type binding is erased before anything runs, so it cannot fetch — the
     * thing this half of the rule is about. And counting it makes the shape the
     * rule asks for unrepresentable: a component that draws from props alone
     * names its props' types, and the type of a server record lives in the API
     * layer, with no house re-export to reach it through (`.claude/rules/
     * architecture.md`: "Data in by prop … **the shape of the props is on
     * you**"). 21 of the 95 frozen violations were this and nothing else.
     *
     * The vue-router half keeps counting type-only imports on purpose
     * (`frontend/AGENTS.md`): there *is* a house alternative — `NavTarget` from
     * `lib/navTarget.ts` — and naming it is what lets the same component render
     * with no router at all. A rule only pays for itself where the fix it asks
     * for exists.
     */
    function isTypeOnly(node) {
      if (node.importKind === 'type' || node.exportKind === 'type') return true
      const specifiers = node.specifiers ?? []
      return specifiers.length > 0 && specifiers.every((s) => s.importKind === 'type')
    }

    /** A static import, a re-export, or a dynamic `import()` — same dependency. */
    function check(node) {
      const source = node.source
      if (!source || (source.type !== 'Literal' && source.type !== 'StringLiteral')) return
      const specifier = source.value
      if (typeof specifier !== 'string') return

      if (options.routerModules.some((name) => specifier === name || specifier.startsWith(`${name}/`))) {
        context.report({ node, message: options.messages.router })
        return
      }

      const resolved = resolveImport(specifier, filename)
      if (resolved === null) return

      // A relative path *into* node_modules (`../../node_modules/vue-router`)
      // is the same package by another spelling.
      const fromRoot = relative(FRONTEND_ROOT, resolved).split(sep).join('/')
      if (
        options.routerModules.some(
          (name) => fromRoot === `node_modules/${name}` || fromRoot.startsWith(`node_modules/${name}/`)
        )
      ) {
        context.report({ node, message: options.messages.router })
        return
      }

      const modulePath = srcModulePath(resolved)
      if (modulePath === null) return
      if (options.apiLayer.includes(modulePath.split('/')[0])) {
        if (isTypeOnly(node)) return
        context.report({ node, message: options.messages.api })
      }
    }

    return {
      ImportDeclaration: check,
      ExportNamedDeclaration: check,
      ExportAllDeclaration: check,
      ImportExpression: check,
    }
  },
}

/** The plugin a flat config registers under `BOUNDARY_PLUGIN_NAME`. */
export const boundaryPlugin = {
  rules: { [BOUNDARY_RULE_ID.slice(BOUNDARY_RULE_ID.indexOf('/') + 1)]: boundaryRule },
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
