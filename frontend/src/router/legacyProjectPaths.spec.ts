// #370 moved the workspace from `/project` to the plural `/projects`. Old links
// keep working through a redirect. The compiler cannot check a redirect — it is
// data, and a wrong one still typechecks.
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
  { path: '/projects/:projectId', name: 'workspace-project', component: Stub },
  { path: '/projects/:projectId/settings', name: 'project-settings', component: Stub },
]

// Same order as router/index.ts: the redirects come first.
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

  it('leaves a current workspace link alone', async () => {
    const r = router()
    await r.push(`/projects/${UUID}`)
    expect(r.currentRoute.value.path).toBe(`/projects/${UUID}`)
    expect(r.currentRoute.value.name).toBe('workspace-project')
  })
})
