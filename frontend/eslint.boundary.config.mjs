// The component boundary rule, as an eslint configuration.
//
// WHY THIS IS NOT IN eslint.config.mjs. The rule is not new — 131 imports in
// src/components/** already break it — so adding it there as `error` would red
// `pnpm run lint`, the pre-commit hook and frontend.yml's ESLint step for
// everybody on day one, on files nobody touched. As a `warn` it would be
// invisible among the ~288 existing warnings. So the rule lives here, in a
// config that is only read by the gate that knows about the baseline:
//
//     pnpm run lint:boundary           check (exit 1 on any NEW violation)
//     pnpm run lint:boundary:update    rewrite the baseline downward
//     pnpm exec eslint --config eslint.boundary.config.mjs src/components
//                                      every violation, baseline ignored
//
// `eslint.config.mjs` stays the config every editor and `pnpm run lint` read;
// this file extends it rather than restating it, so the parser setup, the
// ignores and the plugin wiring cannot drift between the two.
import {
  BOUNDARY_FILES,
  BOUNDARY_IGNORES,
  BOUNDARY_RULE_ID,
  boundaryOptions,
} from './scripts/import-boundary-ratchet-core.mjs'
import base from './eslint.config.mjs'

export default [
  ...base,
  {
    files: BOUNDARY_FILES,
    ignores: BOUNDARY_IGNORES,
    rules: {
      [BOUNDARY_RULE_ID]: ['error', boundaryOptions],
    },
  },
]
