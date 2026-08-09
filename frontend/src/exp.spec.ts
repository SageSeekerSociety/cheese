// 功能旗 (exp.ts): sticky ?exp=true semantics — enter via URL, stay across
// in-app navigation, gate 内测-only routes, plain URLs stay stable.
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { expOnly, installExperimentalGuard } from './exp'

const Stub = { template: '<div />' }

function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'workspace', component: Stub },
      { path: '/market', name: 'market', component: Stub },
      {
        path: '/my/devices',
        name: 'my-devices',
        component: Stub,
        beforeEnter: expOnly,
      },
    ],
  })
  installExperimentalGuard(router)
  return router
}

describe('功能旗 (?exp sticky)', () => {
  it('stays sticky across in-app navigation', async () => {
    const router = makeRouter()
    await router.push({ path: '/', query: { exp: 'true' } })
    await router.push({ name: 'market' })
    expect(router.currentRoute.value.query.exp).toBe('true')
  })

  it('plain navigation stays stable (no flag invented)', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.push({ name: 'market' })
    expect(router.currentRoute.value.query.exp).toBeUndefined()
  })

  it('expOnly route redirects to workspace without the flag', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.push({ name: 'my-devices' })
    expect(router.currentRoute.value.name).toBe('workspace')
  })

  it('expOnly route opens in 内测态', async () => {
    const router = makeRouter()
    await router.push({ path: '/', query: { exp: 'true' } })
    await router.push({ name: 'my-devices' })
    expect(router.currentRoute.value.name).toBe('my-devices')
    expect(router.currentRoute.value.query.exp).toBe('true')
  })
})
