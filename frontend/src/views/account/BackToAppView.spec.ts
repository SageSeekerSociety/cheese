// The view BackToApp.vue renders: the failure, the link that opens the app, the
// account it is handed over to, and the intents it sends back (hand over,
// another account, cancel).
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import BackToAppView from './BackToAppView.vue'

import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(props: { failed?: boolean; link?: string; asking?: boolean; handing?: boolean } = {}) {
  return render(BackToAppView, {
    props: {
      failed: false,
      link: '',
      asking: false,
      handing: false,
      account: { name: 'Andy', handle: 'andylizf' },
      ...props,
    },
    global: { plugins: [createVuetify({ components })] },
  })
}

describe('BackToAppView', () => {
  it('draws a line while the hand-off settles', () => {
    const { container } = mount()
    expect(container.querySelector('.v-progress-linear')).toBeTruthy()
  })

  it('shows the failure and nothing to open', () => {
    const view = mount({ failed: true })
    expect(view.getByText(/could not be handed to the Cheese app/)).toBeTruthy()
    expect(view.queryByRole('link')).toBeNull()
  })

  it('opens the app through the link it was handed', () => {
    const view = mount({ link: 'cheese://open?path=%2Finbox' })
    expect(view.getByRole('link', { name: 'Open Cheese' }).getAttribute('href')).toBe('cheese://open?path=%2Finbox')
  })

  it('names the account and sends the hand-over intent', async () => {
    const view = mount({ asking: true })
    expect(view.getByText(/as Andy \(andylizf\)/)).toBeTruthy()
    await fireEvent.click(view.getByRole('button', { name: 'Continue' }))
    expect(view.emitted('handOver')).toBeTruthy()
  })

  it('sends the switch-account and cancel intents', async () => {
    const view = mount({ asking: true })
    await fireEvent.click(view.getByRole('button', { name: 'Use another account' }))
    await fireEvent.click(view.getByRole('button', { name: 'Cancel' }))
    expect(view.emitted('switchAccount')).toBeTruthy()
    expect(view.emitted('cancel')).toBeTruthy()
  })
})
