// The component boundary rule, as an eslint configuration.
//
// The rule has its own config, and its own gate, so it is run and reported on
// by itself (with the could-not-judge guard and the JSON record the gate adds):
//
//     pnpm run lint:boundary           check (exit 1 on any violation)
//     pnpm exec eslint --config eslint.boundary.config.mjs src/components
//                                      the same rule, eslint's own output
//
// `eslint.config.mjs` stays the config every editor and `pnpm run lint` read;
// this file extends it rather than restating it, so the parser setup, the
// ignores and the plugin wiring cannot drift between the two.
import {
  BOUNDARY_FILES,
  BOUNDARY_IGNORES,
  BOUNDARY_PLUGIN_NAME,
  BOUNDARY_RULE_ID,
  boundaryOptions,
  boundaryPlugin,
} from './scripts/import-boundary-ratchet-core.mjs'
import base from './eslint.config.mjs'

export default [
  ...base,
  {
    files: BOUNDARY_FILES,
    ignores: BOUNDARY_IGNORES,
    // The rule is local (scripts/import-boundary-ratchet-core.mjs) and not
    // eslint's `no-restricted-imports`: that one matches the specifier as a
    // glob, so `@/api` was counted and `../api` — the same dependency, spelled
    // the long way — never was. A rule that resolves the path first needs code,
    // not a pattern, so it travels as a rule object and is registered here.
    plugins: { [BOUNDARY_PLUGIN_NAME]: boundaryPlugin },
    rules: {
      [BOUNDARY_RULE_ID]: ['error', boundaryOptions],
    },
  },
]
