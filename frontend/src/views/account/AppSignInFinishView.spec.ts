// The view AppSignInFinish.vue renders: a line while the app finishes the
// sign-in, and, once that failed, the error and the way back to signing in.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import AppSignInFinishView from './AppSignInFinishView.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

async function mount(failed: boolean) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/account/signin', component: { template: '<div />' } }],
  })
  await router.push('/account/signin')
  await router.isReady()
  return render(AppSignInFinishView, {
    props: { failed },
    global: { plugins: [router, createVuetify({ components })] },
  })
}

describe('AppSignInFinishView', () => {
  it('draws a line while the app finishes the sign-in', async () => {
    const { container } = await mount(false)
    expect(container.querySelector('.v-progress-linear')).toBeTruthy()
    expect(container.querySelector('.v-alert')).toBeNull()
  })

  it('shows the error and the way back to signing in once it failed', async () => {
    const view = await mount(true)
    expect(view.getByText('This sign-in has expired. Sign in again.')).toBeTruthy()
    expect(view.getByRole('link', { name: 'Back to sign in' }).getAttribute('href')).toBe('/account/signin')
  })
})
