// The "nothing touches Web Storage directly" rule, and the pure logic that
// counts violations of it. No I/O, no eslint import — so the rule can be
// declared ONCE and read from two places: `eslint.browser-storage.config.mjs`
// (which turns it into a lint rule) and `browser-storage-ratchet.mjs` (which
// decides whether the counts are allowed). A rule id that drifts between the
// enforcing config and the counting script would silently count nothing.
//
// THE RULE. `localStorage` and `sessionStorage` are reachable from anywhere, so
// 36 files reached for them in their own way: some wrapped the access in
// try/catch, some did not, some had their own JSON parse, some their own
// fallback constant, some a `typeof localStorage === 'undefined'` guard for the
// SSR/desktop case. None of it was wrong on purpose; there was just no module
// to share. `src/lib/browserStorage.ts` is that module, and this rule is what
// keeps it the only one: a direct reference is a new copy of the decision.
//
// WHAT IT COSTS TO GET IT WRONG. A `localStorage` call throws — Safari's
// private mode, a full quota, a site with storage blocked, and any context
// where the global is simply absent. A throw inside a `setup()` or a computed
// takes the render with it; a throw inside a watcher takes the interaction. The
// module never throws: it degrades to "this preference does not persist".
//
// WHY A LOCAL RULE AND NOT `no-restricted-globals`. That rule matches on the
// NAME it is given, in every position — including `'localStorage'` as a string
// and in comments — and cannot tell `window.localStorage` from a local named
// the same. This one looks at identifiers and member expressions, so it counts
// exactly the references that reach the global, and nothing else. Same reason
// the component boundary rule is local (see import-boundary-ratchet-core.mjs).
//
// WHAT IS NOT COUNTED, deliberately:
//   - `useStorage` from @vueuse/core. It is a safe wrapper, and it is what a
//     *reactive, cross-tab* preference should use. It cannot back module-level
//     shared state (`useSidebarWidth` is shared by four sidebars, and
//     `useStorage` returns a fresh ref per call), which is why the module is
//     hand-written — but forbidding it would be forbidding a correct answer.
//   - comments and strings: `// falls back to localStorage` is documentation,
//     not a call.
//   - member names that are not a storage reference: `{ localStorage: mock }`
//     builds an object, it does not read the global. The one exception is the
//     shorthand `{ localStorage }` with nothing after the name, which without
//     scope analysis is indistinguishable from `const { localStorage } = window`
//     — that one form is counted (see isLabel).
//   - spec files and `src/test/**` (see STORAGE_IGNORES): a test that reads
//     `localStorage.getItem(key)` to assert what was persisted is checking the
//     browser, and it is not shipped code.

import { parseEslintJsonFor } from './eslint-ratchet-json.mjs'

/** The eslint rule that carries the storage ban. Must match the id eslint reports. */
export const STORAGE_RULE_ID = 'storage/no-direct-browser-storage'

/** The plugin name half of that id; a flat config registers `storagePlugin` under it. */
export const STORAGE_PLUGIN_NAME = STORAGE_RULE_ID.slice(0, STORAGE_RULE_ID.indexOf('/'))

/**
 * Where the rule applies, relative to the frontend root. Everything under src/:
 * unlike the component boundary this is not about a layer, it is about a
 * global, and a global does not care which directory it is reached from.
 */
export const STORAGE_FILES = ['src/**/*.{vue,ts,tsx,js,jsx}']

/** The one module allowed to touch the global, and the files that are not shipped code. */
export const STORAGE_IGNORES = [
  'src/lib/browserStorage.ts',
  'src/test/**',
  '**/*.spec.ts',
  '**/*.spec.js',
  '**/*.spec.vue',
  '**/*.test.ts',
  '**/*.test.js',
  '**/*.test.vue',
]

// Node types whose non-computed `key`/name position is a label, not a reference:
// `{ localStorage: fake }`, `interface W { localStorage: Storage }`,
// `class A { localStorage = fake }`. Reading a member (`x.localStorage`) is the
// opposite case and is counted — `parent.property` is not a key.
const KEY_POSITION_PARENTS = new Set([
  'PropertySignature',
  'TSPropertySignature',
  'TSMethodSignature',
  'MethodDefinition',
  'PropertyDefinition',
])

/** The rule body, passed to the rule by `eslint.browser-storage.config.mjs`. */
export const storageOptions = {
  /** The globals nobody but src/lib/browserStorage.ts may name. */
  names: ['localStorage', 'sessionStorage'],
  messages: {
    direct:
      'Web Storage must not be reached for directly. localStorage throws in ' +
      'Safari private mode and when the quota is full, and it is absent in a ' +
      'non-browser context — one throw in setup() loses the render. Use ' +
      '@/lib/browserStorage (storageGet / storageSet / storageGetJson / ' +
      'storageSetJson / storageRemove / storageGetStrings): it never throws ' +
      'and degrades to "this preference does not persist".',
  },
}

/** The rule. One rule id, so the config and the counting script cannot drift. */
export const storageRule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Nothing outside src/lib/browserStorage.ts may reference localStorage or sessionStorage directly; the safe module is the only place that accepts their failure modes.',
    },
    schema: [{ type: 'object' }],
  },
  create(context) {
    const options = { ...storageOptions, ...(context.options[0] ?? {}) }
    const names = new Set(options.names ?? storageOptions.names)

    /**
     * A name in key position: a key of an object literal, or a type/class member
     * name. Not a reference — `{ localStorage: fake }` builds an object.
     *
     * The one case not treated as a label is the shorthand `{ localStorage }`:
     * without scope analysis eslint cannot tell an object literal from a
     * destructuring pattern, and the destructuring form (`const { localStorage }
     * = window`) is a reference the rule exists to catch. Over-counting the
     * literal form is the cheaper mistake, and it is the rarer one.
     */
    function isLabel(node) {
      const parent = node.parent
      if (!parent || parent.computed === true) return false
      if (parent.type === 'Property') {
        return parent.key === node && parent.parent?.type === 'ObjectExpression' && parent.shorthand !== true
      }
      return KEY_POSITION_PARENTS.has(parent.type) && parent.key === node
    }

    return {
      Identifier(node) {
        if (!names.has(node.name) || isLabel(node)) return
        // `x.localStorage` — a non-computed member property is the access. It is
        // counted whatever `x` is: the global, `window`, `globalThis`, or a
        // test double that stands in for one.
        context.report({ node, message: options.messages.direct })
      },
      // `window['localStorage']`, the one spelling that hides the name in a string.
      Literal(node) {
        if (typeof node.value !== 'string' || !names.has(node.value)) return
        const parent = node.parent
        if (parent?.type === 'MemberExpression' && parent.computed === true && parent.property === node) {
          context.report({ node, message: options.messages.direct })
        }
      },
    }
  },
}

/** The plugin a flat config registers under `STORAGE_PLUGIN_NAME`. */
export const storagePlugin = {
  rules: { [STORAGE_RULE_ID.slice(STORAGE_RULE_ID.indexOf('/') + 1)]: storageRule },
}

/**
 * Count direct-storage violations per file in eslint's `--format json` output —
 * this rule's share of it. The parsing, the relative-path keys and the "could
 * not judge" set live in `eslint-ratchet-json.mjs`; this binds it to
 * `STORAGE_RULE_ID`.
 *
 * @param {string} text raw eslint JSON output
 * @param {string} root absolute path the file names are made relative to
 * @returns {{ counts: Record<string, number>, violations: {file: string, line: number, column: number, rule: string, message: string}[], unjudged: string[] }}
 */
export function parseStorageEslintJson(text, root = process.cwd()) {
  return parseEslintJsonFor(text, root, STORAGE_RULE_ID)
}

// The ratchet arithmetic is the same question the vue-tsc and stylelint
// ratchets ask of their own counts — a file may not go up, a file that went
// down should have its baseline tightened, and a stale baseline entry is not a
// failure. It lives in one place (tsc-ratchet-core.mjs) rather than in another
// copy that can drift; a second definition of "regression" is a second answer
// to what the baseline means.
export { compare, tightenedBaseline } from './tsc-ratchet-core.mjs'

/**
 * The human-readable half: what went wrong, where, and what to do about it.
 * @param {ReturnType<typeof compare>} result
 * @param {{file: string, line: number, column: number, rule: string, message: string}[]} [details]
 */
export function formatStorageReport(result, details = []) {
  const lines = []
  if (result.regressions.length) {
    lines.push('New direct Web Storage references (this is what the ratchet blocks):')
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
    lines.push('Import from @/lib/browserStorage instead: storageGet, storageSet,')
    lines.push('storageGetJson, storageSetJson, storageRemove, storageGetStrings.')
    lines.push('It never throws, so a blocked or full storage just means the')
    lines.push('preference does not persist. Keep the key and the value format the')
    lines.push('same, so what users already have stored keeps working. If a file')
    lines.push('genuinely has to touch the storage object itself, the place for that')
    lines.push('is src/lib/browserStorage.ts — not a line in')
    lines.push('browser-storage-baseline.json, which only ever goes down.')
    lines.push('The rule and its messages: scripts/browser-storage-ratchet-core.mjs.')
  }
  if (result.improvements.length) {
    lines.push(
      result.regressions.length ? 'Also improved:' : 'Direct storage references went down — tighten the baseline:'
    )
    for (const { file, base, now } of result.improvements) {
      lines.push(`  ${file}: ${base} -> ${now}`)
    }
    lines.push('')
    lines.push('Run: pnpm run lint:storage:update  (then commit browser-storage-baseline.json)')
  }
  lines.push(`total: ${result.currentTotal} reference(s), baseline allows ${result.baselineTotal}`)
  return lines.join('\n')
}
