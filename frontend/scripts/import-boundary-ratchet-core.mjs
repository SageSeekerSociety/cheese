// The component boundary rule, and the pure logic that counts violations of it.
// No I/O, no eslint import — so the rule can be declared ONCE and read from two
// places: `eslint.boundary.config.mjs` (which turns it into a lint rule) and
// `import-boundary-ratchet.mjs` (which runs it and fails on any violation).
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

import { parseEslintJsonFor } from './eslint-ratchet-json.mjs'

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
     * you**"). When this half was written, 21 of the 95 frozen violations were
     * this and nothing else.
     *
     * The vue-router half keeps counting type-only imports on purpose: there
     * *is* a house alternative — `NavTarget` from `lib/navTarget.ts` — and
     * naming it is what lets the same component render with no router at all.
     * A rule only pays for itself where the fix it asks for exists.
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
 * Count boundary violations per file in eslint's `--format json` output — this
 * rule's share of it. The parsing, the relative-path keys and the "could not
 * judge" set are the same question every rule-counting ratchet asks, so they
 * live in `eslint-ratchet-json.mjs`; this binds it to `BOUNDARY_RULE_ID`.
 *
 * @param {string} text raw eslint JSON output
 * @param {string} root absolute path the file names are made relative to
 * @returns {{ counts: Record<string, number>, violations: {file: string, line: number, column: number, rule: string, message: string}[], unjudged: string[] }}
 */
export function parseEslintJson(text, root = process.cwd()) {
  return parseEslintJsonFor(text, root, BOUNDARY_RULE_ID)
}

/**
 * The human-readable half: every violation, and what to do about it.
 *
 * The reader is whoever has to land the fix, an agent as often as a person, and
 * the report is the whole of what they get: it names the shortest fix and the
 * one-file command that proves it.
 * @param {{file: string, line: number, column: number, rule: string, message: string}[]} violations
 */
export function formatBoundaryReport(violations) {
  if (!violations.length) return 'no component boundary violations'
  const lines = ['Component boundary violations:']
  for (const { file, line, column, rule, message } of violations) {
    lines.push(`  ${file}:${line}:${column}  ${rule}  ${message}`)
  }
  lines.push('')
  lines.push('Sink the dependency. What was crossed is the API layer (@/api,')
  lines.push('@/services/*) or the router, and both sink the same way:')
  lines.push('  - Fetching belongs to the view (src/views/**) or to a composable the')
  lines.push('    view calls (src/composables/), or a store (src/stores/) when several')
  lines.push('    components share one request. The component takes the data as a prop')
  lines.push('    and emits the intent back up.')
  lines.push('  - The route moves up the same way: read it above and pass a NavTarget')
  lines.push('    (src/lib/navTarget.ts) down; use useNavigation() (src/composables/)')
  lines.push('    only where a component really must navigate, and keep vue-router —')
  lines.push('    even `import type` — out of src/components.')
  lines.push('  - Why, and what the boundary buys: .claude/rules/architecture.md')
  lines.push('    (frontend: a component does not fetch). The rule and its messages')
  lines.push('    live in scripts/import-boundary-ratchet-core.mjs.')
  lines.push('')
  lines.push('Check the one file you are fixing, from frontend/ — not the whole tree:')
  for (const file of [...new Set(violations.map((v) => v.file))]) {
    lines.push(`  node scripts/import-boundary-ratchet.mjs --files ${file}`)
  }
  lines.push(`total: ${violations.length} violation(s)`)
  return lines.join('\n')
}
