// Redirects for the project URL #370 retired, kept in their own module so the
// spec next door exercises THESE records rather than a copy of them — a routing
// rule that is only tested through a duplicate is a rule nothing checks.
//
// The AI workspace moved from the singular `/project/<uuid>` to `/projects/<uuid>`.
// Unlike the API side of the same rename, this costs nothing to keep: vue-router
// resolves it inside one bundle, and nginx serves index.html for unrecognised
// paths either way.
import type { RouteRecordRaw } from 'vue-router'

export const legacyProjectRedirects: RouteRecordRaw[] = [
  {
    // Every workspace link ever pasted into a chat or bookmarked. The
    // most-visited URL in the product, which is why the rename carries
    // redirects at all.
    path: '/project/:rest(.*)',
    redirect: (to) => ({ path: `/projects/${to.params.rest}`, query: to.query, hash: to.hash }),
  },
]
