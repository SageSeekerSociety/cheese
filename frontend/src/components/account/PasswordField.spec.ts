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

function mount() {
  // `label` and `hint` are not props of their own: they pass through to the text field.
  return render(PasswordField, {
    props: { autocomplete: 'current-password' },
    attrs: { label: 'Password', hint: 'At least 8 characters', persistentHint: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function withCaps<E extends Event>(event: E, capsOn: boolean): E {
  Object.defineProperty(event, 'getModifierState', { value: (k: string) => k === 'CapsLock' && capsOn })
  return event
}
const key = (type: 'keydown' | 'keyup', capsOn: boolean, k = 'a') =>
  withCaps(new KeyboardEvent(type, { key: k, bubbles: true }), capsOn)

describe('password field and Caps Lock', () => {
  it('warns while Caps Lock is on and stops once it is off', async () => {
    const view = mount()
    const input = view.getByLabelText('Password')
    await fireEvent.focus(input)

    await fireEvent(input, key('keydown', true))
    expect(view.getByRole('status').textContent).toBe('Caps Lock is on')
    expect(view.getByText('Caps Lock')).toBeTruthy()

    await fireEvent(input, key('keydown', false))
    expect(view.getByRole('status').textContent).toBe('')
    expect(view.queryByText('Caps Lock')).toBeNull()
  })

  it('knows as soon as the field is clicked, before anything is typed', async () => {
    const view = mount()
    const input = view.getByLabelText('Password')

    await fireEvent(input, withCaps(new MouseEvent('mousedown', { bubbles: true }), true))

    expect(view.getByRole('status').textContent).toBe('Caps Lock is on')
  })

  it('turns on with the Caps Lock key even where that keydown reports the old state', async () => {
    const view = mount()
    const input = view.getByLabelText('Password')
    await fireEvent.focus(input)

    await fireEvent(input, key('keydown', false, 'CapsLock'))
    expect(view.getByRole('status').textContent).toBe('Caps Lock is on')

    await fireEvent(input, key('keyup', true, 'CapsLock'))
    expect(view.getByRole('status').textContent).toBe('Caps Lock is on')
  })

  it('leaves the field’s own hint in place while warning', async () => {
    const view = mount()
    const input = view.getByLabelText('Password')
    await fireEvent.focus(input)

    await fireEvent(input, key('keydown', true))

    expect(view.getByText('At least 8 characters')).toBeTruthy()
  })
})
