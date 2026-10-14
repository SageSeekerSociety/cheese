// The accessibility rules (eslint-plugin-vuejs-accessibility), as an eslint
// configuration.
//
// WHY THIS IS NOT IN eslint.config.mjs. The tree already breaks these rules in
// about 159 places, so adding them there as `error` would red `pnpm run lint`,
// the pre-commit hook and frontend.yml's ESLint step for everybody on day one,
// on files nobody touched; as `warn` they would vanish among the existing
// warnings. So they live here, in a config read only by the gate that knows
// about the baseline — the same arrangement as eslint.boundary.config.mjs:
//
//     pnpm run lint:a11y           check (exit 1 on any NEW violation)
//     pnpm run lint:a11y:update    rewrite the baseline downward
//     pnpm exec eslint --config eslint.a11y.config.mjs 'src/**/*.vue'
//                                  every violation, baseline ignored
//
// It extends eslint.config.mjs rather than restating it, so the parser setup
// (TypeScript inside <script lang="ts">) and the ignores cannot drift between
// the two. The rule set is the plugin's `flat/recommended`, unmodified.
import a11y from 'eslint-plugin-vuejs-accessibility'

import base from './eslint.config.mjs'

export default [
  ...base,
  ...a11y.configs['flat/recommended'],
  {
    // An unused `eslint-disable` is the main lint's business and it already
    // reports it. Here it would arrive as a message with no rule id, which the
    // ratchet treats as "could not judge this file".
    linterOptions: { reportUnusedDisableDirectives: 'off' },
  },
]
