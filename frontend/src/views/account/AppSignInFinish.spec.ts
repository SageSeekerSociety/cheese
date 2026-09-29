// The end of a sign-in made in the browser, in the app: a code alone signs
// nobody in. Only a sign-in this app started, with the secret it kept, does.
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import AppSignInFinish from './AppSignInFinish.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({ UserApi: { finishAppSignIn: vi.fn(async () => ({ data: null })) } }))
vi.mock('@/services/account', () => ({ default: { resumeFromCookie: vi.fn(async () => 'ok') } }))

async function arrive(query: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/oauth/from-browser', component: AppSignInFinish },
      { path: '/:rest(.*)*', component: { template: '<div />' } },
    ],
  })
  await router.push(`/account/oauth/from-browser?${query}`)
  render(AppSignInFinish, { global: { plugins: [router, createVuetify({ components, directives })] } })
  return router
}

beforeEach(() => setLocale('zh-CN'))

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
})

describe('AppSignInFinish', () => {
  it('signs nobody in from a link the app did not ask for', async () => {
    await arrive('code=from-somewhere')
    await vi.waitFor(() => expect(document.body.textContent).toContain('登录'))
    expect(UserApi.finishAppSignIn).not.toHaveBeenCalled()
  })

  it('finishes the sign-in this app started, with its secret, and goes where it was headed', async () => {
    localStorage.setItem('cheese.appSignIn', JSON.stringify({ verifier: 'kept-secret', target: '/inbox' }))
    const router = await arrive('code=the-code')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/inbox'))
    expect(UserApi.finishAppSignIn).toHaveBeenCalledWith('the-code', 'kept-secret')
  })
})
