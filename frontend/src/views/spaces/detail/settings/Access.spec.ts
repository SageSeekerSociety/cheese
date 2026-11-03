// The space settings' "access" section: members only, or open to email
// suffixes. Opening waits for platform review, so the section must say a
// request is waiting, send the choice the owner made, and keep the space as the
// server returns it.
//
// Labels go through keys (i18n is replaced below by `t(key) => key`).
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

const setAccess = vi.fn()

vi.mock('@/api/spaces', () => ({
  SpacesApi: { setAccess: (...a: unknown[]) => setAccess(...a) },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import Access from './Access.vue'

import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 52

function mountPage(space: Record<string, unknown>) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSpaceStore(pinia)
  store.setSpace({ id: SPACE_ID, name: 'eTrip', ...space } as never)
  const utils = render(Access, {
    global: { plugins: [pinia, createVuetify({ components, directives })] },
  })
  return { ...utils, store }
}

/** Pick a Vuetify radio: happy-dom's click alone does not reach VSelectionControl's
 *  input handler, so the input and change events go too (as McpServerDialog.spec). */
async function choose(radio: HTMLInputElement) {
  await fireEvent.click(radio)
  radio.checked = true
  await fireEvent.input(radio)
  await fireEvent.change(radio)
}

function saveButton() {
  const button = Array.from(document.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === 'spaces.access.save'
  )
  expect(button, 'no save button').toBeTruthy()
  return button as HTMLButtonElement
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('space settings: access', () => {
  it('opening a members-only space sends the suffixes the owner typed', async () => {
    const pending = { id: 3, scope: ['ruc.edu.cn'], status: 'PENDING', reason: null, createdAt: '', reviewedAt: null }
    setAccess.mockResolvedValue({ space: { id: SPACE_ID, name: 'eTrip', openScope: null, openRequest: pending } })
    const view = mountPage({ openScope: null, openRequest: null })

    await choose(view.getByRole('radio', { name: /spaces.access.open\b/ }) as HTMLInputElement)
    await fireEvent.update(view.getByLabelText(/spaces.access.scope\b/), 'ruc.edu.cn\n mails.ruc.edu.cn ')
    await fireEvent.click(saveButton())

    await waitFor(() =>
      expect(setAccess).toHaveBeenCalledWith(SPACE_ID, { mode: 'open', scope: ['ruc.edu.cn', 'mails.ruc.edu.cn'] })
    )
    // The server kept the space members-only and filed a request; the page says so.
    expect(await view.findByText('spaces.access.pending')).toBeTruthy()
    expect(view.store.currentSpace?.openScope).toBeNull()
  })

  it('an open space with no suffix is not sent', async () => {
    const view = mountPage({ openScope: null, openRequest: null })

    await choose(view.getByRole('radio', { name: /spaces.access.open\b/ }) as HTMLInputElement)
    await fireEvent.click(saveButton())

    expect(await view.findByText('spaces.access.scopeRequired')).toBeTruthy()
    expect(setAccess).not.toHaveBeenCalled()
  })

  it('going back to members only sends no scope', async () => {
    setAccess.mockResolvedValue({ space: { id: SPACE_ID, name: 'eTrip', openScope: null, openRequest: null } })
    const view = mountPage({ openScope: ['ruc.edu.cn'], openRequest: null })

    await choose(view.getByRole('radio', { name: /spaces.access.members\b/ }) as HTMLInputElement)
    await fireEvent.click(saveButton())

    await waitFor(() => expect(setAccess).toHaveBeenCalledWith(SPACE_ID, { mode: 'members', scope: [] }))
  })

  it('a rejected request shows why', () => {
    const rejected = {
      id: 4,
      scope: ['ruc.edu.cn'],
      status: 'REJECTED',
      reason: 'no owner named',
      createdAt: '',
      reviewedAt: '',
    }
    const view = mountPage({ openScope: null, openRequest: rejected })

    expect(view.getByText('spaces.access.rejected')).toBeTruthy()
  })
})
