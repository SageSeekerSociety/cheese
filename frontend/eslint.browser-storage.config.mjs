// The "nothing touches Web Storage directly" rule, as an eslint configuration.
//
// The rule has its own config, and its own gate, so it is run and reported on
// by itself (with the could-not-judge guard and the JSON record the gate adds):
//
//     pnpm run lint:storage           check (exit 1 on any reference)
//     pnpm exec eslint --config eslint.browser-storage.config.mjs src
//                                     the same rule, eslint's own output
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
