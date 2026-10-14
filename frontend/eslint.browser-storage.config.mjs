// The "nothing touches Web Storage directly" rule, as an eslint configuration.
//
// WHY THIS IS NOT IN eslint.config.mjs. The rule is not new — the sites it
// counts are pre-existing and there are more of them than there are files to
// migrate in one go — so adding it there as `error` would red `pnpm run lint`,
// the pre-commit hook and frontend.yml's ESLint step for everybody on day one,
// on files nobody touched. As a `warn` it would be invisible among the existing
// warnings. So the rule lives here, in a config only the gate that knows about
// the baseline reads:
//
//     pnpm run lint:storage           check (exit 1 on any NEW reference)
//     pnpm run lint:storage:update    rewrite the baseline downward
//     pnpm exec eslint --config eslint.browser-storage.config.mjs src
//                                     every reference, baseline ignored
//
// `eslint.config.mjs` stays the config every editor and `pnpm run lint` read;
// this file extends it rather than restating it, so the parser setup, the
// ignores and the plugin wiring cannot drift between the two.
import {
  STORAGE_FILES,
  STORAGE_IGNORES,
  STORAGE_PLUGIN_NAME,
  STORAGE_RULE_ID,
  storageOptions,
  storagePlugin,
} from './scripts/browser-storage-ratchet-core.mjs'
import base from './eslint.config.mjs'

export default [
  ...base,
  {
    files: STORAGE_FILES,
    ignores: STORAGE_IGNORES,
    // The rule is local (scripts/browser-storage-ratchet-core.mjs) and not
    // eslint's `no-restricted-globals`: that one matches the name in every
    // position — strings and comments included — which would count
    // `// falls back to localStorage` and miss nothing it should not, while
    // this one counts exactly the references that reach the global. A rule that
    // has to tell an access from a label needs code, not a pattern.
    plugins: { [STORAGE_PLUGIN_NAME]: storagePlugin },
    rules: {
      [STORAGE_RULE_ID]: ['error', storageOptions],
    },
  },
]
