// Redirects for the project URLs #370 retired, kept in their own module so the
// spec next door exercises THESE records rather than a copy of them — a routing
// rule that is only tested through a duplicate is a rule nothing checks.
//
// Two renames happened at once in the address bar:
//   知是 团队项目   /projects/<int>  →  /team-projects/<int>
//   AI 工作台       /project/<uuid>  →  /projects/<uuid>
//
// Which means the old 1.0 path and the new 2.0 path are spelled identically and
// only the id tells them apart: ints are 团队项目, uuids are workspaces. Without
// the numeric rules below, every old 团队项目 bookmark opens the workspace on an
// id it can never resolve — the wrong-generation load this rename exists to end.
//
// Unlike the API side of the same rename, these cost nothing to keep: vue-router
// resolves them inside one bundle, so they shadow no other generation's
// namespace, and nginx serves index.html for unrecognised paths either way.
import type { RouteRecordRaw } from 'vue-router'

export const legacyProjectRedirects: RouteRecordRaw[] = [
  {
    // Every workspace link ever pasted into a chat or bookmarked. The
    // most-visited URL in the product, which is why the rename carries
    // redirects at all.
    path: '/project/:rest(.*)',
    redirect: (to) => ({ path: `/projects/${to.params.rest}`, query: to.query, hash: to.hash }),
  },
  {
    // Declared before the workspace routes so the numeric form wins the match
    // outright instead of relying on vue-router's scoring.
    path: '/projects/:projectId(\\d+)',
    redirect: (to) => ({ path: `/team-projects/${to.params.projectId}`, query: to.query }),
  },
  {
    path: '/projects/:projectId(\\d+)/:rest(.*)',
    redirect: (to) => ({
      path: `/team-projects/${to.params.projectId}/${to.params.rest}`,
      query: to.query,
    }),
  },
]
