// 功能旗 (内测开关) — same semantics as the main repo's ?exp=true pattern:
// a URL carrying `?exp=true` enters 内测态; the guard keeps the flag STICKY
// across in-app navigation (every router.push inherits it), so once you're in,
// you stay in — while a plain link stays on the stable surface. Nothing is
// stored; dropping the param (fresh plain URL) exits.
//
// Gate rule: routes add `beforeEnter: expOnly`; UI entries hide behind
// `useExperimental()`. Gate ONLY genuinely 内测 surfaces — the default app is
// the product, not a teaser.
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import type { NavigationGuardWithThis, Router } from 'vue-router'

/** Reactive: is the current navigation in 内测态 (?exp=true)? */
export function useExperimental() {
  const route = useRoute()
  return computed(() => route.query.exp === 'true')
}

/** Route guard for 内测-only routes: without the flag, land on the workspace. */
export const expOnly: NavigationGuardWithThis<undefined> = (to) => {
  if (to.query.exp === 'true') return true
  return { name: 'workspace' }
}

/** Keep `exp=true` sticky across in-app navigation (installed once in main.ts). */
export function installExperimentalGuard(router: Router) {
  router.beforeEach((to, from) => {
    if (from.query.exp === 'true' && to.query.exp === undefined) {
      return { ...to, query: { ...to.query, exp: 'true' } }
    }
    return true
  })
}
