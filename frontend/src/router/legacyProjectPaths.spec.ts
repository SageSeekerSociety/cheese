// #370 renamed both project URLs in the address bar: 知是 团队项目 moved from
// `/projects` to `/team-projects`, and the workspace took the plural it had been
// avoiding (`/project` → `/projects`). Old links keep working through redirects.
//
// The compiler cannot check a redirect — it is data, and a wrong one still
// typechecks. What earns this a test is the collision the rename creates on
// purpose: an OLD 团队项目 link and a NEW workspace link are now spelled
// identically, and only the id's shape tells them apart. Get that wrong and the
// app opens the workspace on an id it can never resolve — the wrong-generation
// load this rename exists to end.
//
// The records under test are IMPORTED, not restated: a routing rule checked
// through a copy of itself is a rule nothing checks. Only the destinations are
// stubbed, and the assertions are on the resolved path, not on what rendered.
import type { RouteRecordRaw } from 'vue-router'

import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { legacyProjectRedirects } from './legacyProjectPaths'

const Stub = { template: '<div />' }

const destinations: RouteRecordRaw[] = [
  { path: '/team-projects/:projectId', name: 'team-project', component: Stub },
  { path: '/team-projects/:projectId/members', name: 'team-members', component: Stub },
  { path: '/projects/:projectId', name: 'workspace-project', component: Stub },
  { path: '/projects/:projectId/settings', name: 'project-settings', component: Stub },
]

// Same order as router/index.ts: the redirects come first, because one of them
// matches the very path shape the workspace route claims.
function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [...legacyProjectRedirects, ...destinations],
  })
}

const UUID = '3f1a7c62-9d4e-4b8a-8f21-0c5d6e7a9b10'

describe('retired project URLs', () => {
  it('sends an old workspace link to the plural path', async () => {
    const r = router()
    await r.push(`/project/${UUID}`)
    expect(r.currentRoute.value.path).toBe(`/projects/${UUID}`)
    expect(r.currentRoute.value.name).toBe('workspace-project')
  })

  it('keeps the sub-page, query and hash of an old workspace link', async () => {
    const r = router()
    await r.push(`/project/${UUID}/settings?tab=github#connect`)
    expect(r.currentRoute.value.path).toBe(`/projects/${UUID}/settings`)
    expect(r.currentRoute.value.query.tab).toBe('github')
    expect(r.currentRoute.value.hash).toBe('#connect')
  })

  it('sends an old 团队项目 link (int id) to /team-projects, not the workspace', async () => {
    const r = router()
    await r.push('/projects/42')
    expect(r.currentRoute.value.path).toBe('/team-projects/42')
    expect(r.currentRoute.value.name).toBe('team-project')
  })

  it('does the same for a sub-page of an old 团队项目 link', async () => {
    const r = router()
    await r.push('/projects/42/members')
    expect(r.currentRoute.value.path).toBe('/team-projects/42/members')
    expect(r.currentRoute.value.name).toBe('team-members')
  })

  it('leaves a real workspace id alone — a uuid is never mistaken for an int', async () => {
    const r = router()
    await r.push(`/projects/${UUID}`)
    expect(r.currentRoute.value.path).toBe(`/projects/${UUID}`)
    expect(r.currentRoute.value.name).toBe('workspace-project')
  })
})
