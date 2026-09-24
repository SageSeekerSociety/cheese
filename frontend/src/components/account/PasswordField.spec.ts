/** Caps Lock is the one reason a right password is refused that the person
 *  cannot see, so the field says so while it is on — and only then. */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PasswordField from './PasswordField.vue'

import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(props: Record<string, unknown> = {}) {
  // `label` is not a prop of its own: it passes through to the text field.
  return render(PasswordField, {
    props: { autocomplete: 'current-password', ...props },
    attrs: { label: 'Password' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function key(capsOn: boolean) {
  const event = new KeyboardEvent('keydown', { key: 'a', bubbles: true })
  Object.defineProperty(event, 'getModifierState', { value: (k: string) => k === 'CapsLock' && capsOn })
  return event
}

describe('password field and Caps Lock', () => {
  it('warns while Caps Lock is on and stops once it is off', async () => {
    const view = mount()
    const input = view.getByLabelText('Password')
    await fireEvent.focus(input)

    await fireEvent(input, key(true))
    expect(await view.findByText('Caps Lock is on')).toBeTruthy()

    await fireEvent(input, key(false))
    expect(view.queryByText('Caps Lock is on')).toBeNull()
  })

  it('gives the field its own hint back when Caps Lock goes off', async () => {
    const view = mount({ hint: 'At least 8 characters', persistentHint: true })
    const input = view.getByLabelText('Password')
    await fireEvent.focus(input)

    await fireEvent(input, key(true))
    expect(await view.findByText('Caps Lock is on')).toBeTruthy()
    expect(view.queryByText('At least 8 characters')).toBeNull()

    await fireEvent(input, key(false))
    expect(await view.findByText('At least 8 characters')).toBeTruthy()
  })
})
