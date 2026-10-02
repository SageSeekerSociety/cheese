// In the desktop app, signing in opens the person's browser. The address it
// opens carries only the hash of a secret the app keeps, so it can also be
// copied into a browser by hand.
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DesktopSignIn from './DesktopSignIn.vue'

import { setLocale } from '@/i18n'
import { takeSignInVerifier } from '@/lib/desktopApp'

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }

beforeEach(() => {
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  ;(window as AppWindow).__TAURI__ = { core: { invoke: async () => undefined } }
  ;(window as AppWindow).__CHEESE_APP__ = { can: ['links'] }
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
  delete (window as AppWindow).__TAURI__
  delete (window as AppWindow).__CHEESE_APP__
})

async function open(query: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/account/app', component: DesktopSignIn }],
  })
  await router.push(`/account/app?${query}`)
  await router.isReady()
  return render(DesktopSignIn, { global: { plugins: [router, createVuetify({ components, directives })] } })
}

describe('signing in to the desktop app', () => {
  it('opens the browser on the page it started from, keeping the secret in the app', async () => {
    const opened = vi.spyOn(window, 'open').mockReturnValue(null)
    const view = await open('entry=signup&redirect=/inbox')

    await fireEvent.click(view.getByRole('button', { name: /Sign up with your browser/ }))

    await waitFor(() => expect(opened).toHaveBeenCalledTimes(1))
    const url = new URL(String(opened.mock.calls[0][0]))
    expect(url.pathname).toBe('/account/oauth/app')
    expect(url.searchParams.get('entry')).toBe('signup')
    const kept = takeSignInVerifier()
    expect(kept?.target).toBe('/inbox')
    expect(url.href).not.toContain(kept!.verifier)
  })

  it('opens the same address again, so the browser tab already open still works', async () => {
    const opened = vi.spyOn(window, 'open').mockReturnValue(null)
    const view = await open('entry=signin')

    await fireEvent.click(view.getByRole('button', { name: /Sign in with your browser/ }))
    await waitFor(() => expect(view.getByRole('button', { name: /Open the browser again/ })).toBeTruthy())
    await fireEvent.click(view.getByRole('button', { name: /Open the browser again/ }))

    expect(opened).toHaveBeenCalledTimes(2)
    expect(opened.mock.calls[1][0]).toBe(opened.mock.calls[0][0])
  })

  it('copies that address for a browser the app cannot open', async () => {
    const opened = vi.spyOn(window, 'open').mockReturnValue(null)
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const view = await open('entry=signin')

    await fireEvent.click(view.getByRole('button', { name: /Sign in with your browser/ }))
    await waitFor(() => expect(view.getByRole('button', { name: /Copy the link/ })).toBeTruthy())
    await fireEvent.click(view.getByRole('button', { name: /Copy the link/ }))

    expect(writeText).toHaveBeenCalledWith(opened.mock.calls[0][0])
  })
})
