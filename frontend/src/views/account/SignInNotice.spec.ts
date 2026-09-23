import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SignIn from './SignIn.vue'

import { setLocale } from '@/i18n'

vi.mock('@/network/api/users', () => ({
  UserApi: { getOAuthProviders: vi.fn().mockResolvedValue({ data: { providers: [] } }) },
}))
vi.mock('@/services/account', () => ({ default: { loggedIn: false } }))

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openSignIn(query: Record<string, string>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account/signin', component: SignIn },
      { path: '/legal/terms', name: 'LegalTerms', component: SignIn },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: SignIn },
    ],
  })
  await router.push({ path: '/account/signin', query })
  await router.isReady()
  return render(SignIn, {
    global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
  })
}

describe('sign-in notice', () => {
  it('shows the notice a known key names', async () => {
    const view = await openSignIn({ message: 'passwordReset' })
    expect(view.getByText('Your password has been reset. Sign in with the new one.')).toBeTruthy()
  })

  it('never puts text from the link on the page', async () => {
    const forged = 'Your account is frozen, call 555-0100'
    const view = await openSignIn({ message: forged })
    expect(view.queryByText(forged, { exact: false })).toBeNull()
    expect(view.container.querySelector('.v-alert')).toBeNull()
  })
})
