// The view Verify.vue renders: the new password entered twice, with the
// account's name riding along for password managers. The page reads the token
// and sends the result.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import VerifyView from './VerifyView.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(props: { username?: string; error?: string; submitting?: boolean } = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/account/signin', name: 'SignIn', component: { template: '<div />' } },
    ],
  })
  return render(VerifyView, {
    props: { username: 'andy', error: '', submitting: false, ...props },
    global: { plugins: [router, createVuetify({ components, directives })] },
  })
}

describe('VerifyView', () => {
  it('shows the two password fields and names the account for password managers', () => {
    const view = mount()
    expect(view.getByText('Set a new password')).toBeTruthy()
    expect(view.getByLabelText('New password')).toBeTruthy()
    expect(view.getByLabelText('Confirm password')).toBeTruthy()
    expect(view.container.querySelector<HTMLInputElement>('input[name="username"]')?.value).toBe('andy')
  })

  it('sends the new password back to the page once it is typed twice', async () => {
    const view = mount()
    await fireEvent.update(view.getByLabelText('New password'), 'Secret#123')
    await fireEvent.update(view.getByLabelText('Confirm password'), 'Secret#123')
    expect(view.getByRole('button', { name: 'Reset password' }).getAttribute('type')).toBe('submit')
    await fireEvent.submit(view.container.querySelector('form')!)
    await waitFor(() => expect(view.emitted('submit')).toEqual([['Secret#123']]))
  })

  it('says the two passwords differ and sends nothing', async () => {
    const view = mount()
    await fireEvent.update(view.getByLabelText('New password'), 'Secret#123')
    await fireEvent.update(view.getByLabelText('Confirm password'), 'Secret#124')
    await fireEvent.submit(view.container.querySelector('form')!)
    expect(await view.findByText('Passwords do not match')).toBeTruthy()
    expect(view.emitted('submit')).toBeFalsy()
  })

  it('shows the sentence it was handed for a reset that failed', () => {
    const view = mount({ error: 'This reset link is invalid or has expired' })
    expect(view.getByText('This reset link is invalid or has expired')).toBeTruthy()
  })

  it('draws the submit button busy while the page waits on the server', () => {
    const view = mount({ submitting: true })
    expect(view.container.querySelector('button.v-btn--loading')).toBeTruthy()
  })
})
