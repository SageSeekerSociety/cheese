// The view Start.vue renders: the reset-request form and the confirmation that
// replaces it once the mail is out. The page hands it the state and takes back
// the intent to send.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import StartView from './StartView.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(props: { error?: string; sent?: boolean; submitting?: boolean } = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/account/signin', name: 'SignIn', component: { template: '<div />' } },
    ],
  })
  return render(StartView, {
    props: { error: '', sent: false, submitting: false, ...props },
    global: { plugins: [router, createVuetify({ components, directives })] },
  })
}

describe('StartView', () => {
  it('shows the reset-request form while the mail is not out yet', () => {
    const view = mount()
    expect(view.getByText('Reset password')).toBeTruthy()
    expect(view.getByLabelText('Account email')).toBeTruthy()
    expect(view.getByRole('button', { name: 'Send reset email' })).toBeTruthy()
  })

  it('sends the address it collected back to the page', async () => {
    const view = mount()
    await fireEvent.update(view.getByLabelText('Account email'), 'user@example.com')
    expect(view.getByRole('button', { name: 'Send reset email' }).getAttribute('type')).toBe('submit')
    await fireEvent.submit(view.container.querySelector('form')!)
    await waitFor(() => expect(view.emitted('submit')).toEqual([['user@example.com']]))
  })

  it('gives way to the confirmation once the mail is out', () => {
    const view = mount({ sent: true })
    expect(view.getByText('Check your email')).toBeTruthy()
    expect(view.getByText('Reset email sent. Check your inbox.')).toBeTruthy()
    expect(view.queryByLabelText('Account email')).toBeNull()
  })

  it('shows the sentence it was handed for a send that failed', () => {
    const view = mount({ error: 'Could not reach the server' })
    expect(view.getByText('Could not reach the server')).toBeTruthy()
  })

  it('draws the submit button busy while the page waits on the server', () => {
    const view = mount({ submitting: true })
    expect(view.container.querySelector('button.v-btn--loading')).toBeTruthy()
  })
})
