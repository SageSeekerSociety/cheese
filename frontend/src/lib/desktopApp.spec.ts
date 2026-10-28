import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  desktopListenForNotices,
  desktopStopNotices,
  goAuthorize,
  markTitleBarOverlay,
  signInInBrowser,
  takeSignInVerifier,
  tellDesktopTheme,
} from './desktopApp'

type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }

// The web app ships before the desktop app updates, so a page may run in an app
// that does not have a command yet. It must not call one such an app lacks.
function desktopApp(about?: object) {
  const invoke = vi.fn<(cmd: string, args?: Record<string, unknown>) => Promise<undefined>>(async () => undefined)
  ;(window as AppWindow).__TAURI__ = { core: { invoke } }
  if (about) (window as AppWindow).__CHEESE_APP__ = about
  return invoke
}

afterEach(() => {
  delete (window as AppWindow).__TAURI__
  delete (window as AppWindow).__CHEESE_APP__
  document.documentElement.classList.remove('title-bar-overlay')
})

describe('markTitleBarOverlay', () => {
  it('marks the page when the app draws its window buttons over it', () => {
    desktopApp({ origin: 'https://okcheese.com', titleBar: 'overlay' })
    markTitleBarOverlay()
    expect(document.documentElement.classList.contains('title-bar-overlay')).toBe(true)
  })

  it('leaves a window whose title bar is native alone, and a browser too', () => {
    markTitleBarOverlay()
    expect(document.documentElement.classList.contains('title-bar-overlay')).toBe(false)

    desktopApp({ origin: 'https://okcheese.com', titleBar: 'native' })
    markTitleBarOverlay()
    expect(document.documentElement.classList.contains('title-bar-overlay')).toBe(false)
  })
})

describe('tellDesktopTheme', () => {
  it('hands the picked theme to an app that follows it', () => {
    const invoke = desktopApp({ origin: 'https://okcheese.com', theme: 'system', titleBar: 'native' })
    tellDesktopTheme('dark')
    expect(invoke).toHaveBeenCalledWith('set_theme', { preference: 'dark' })
  })

  it('leaves an app from before the theme was followed alone', () => {
    const invoke = desktopApp()
    tellDesktopTheme('dark')
    expect(invoke).not.toHaveBeenCalled()
  })

  it('does nothing in a browser', () => {
    expect(() => tellDesktopTheme('light')).not.toThrow()
  })
})

describe('desktop notices', () => {
  const app = { origin: 'https://okcheese.com', theme: 'system', titleBar: 'native', can: ['notices', 'badge'] }

  it("hands the app a credential for the signed-in person's notices", async () => {
    const invoke = desktopApp(app)
    await desktopListenForNotices(7, async () => 'notices-credential')
    expect(invoke).toHaveBeenCalledWith('listen_for_notices', { token: 'notices-credential', account: 7 })
  })

  it('does not even ask for a credential in an app that cannot listen', async () => {
    const invoke = desktopApp({ ...app, can: [] })
    const issue = vi.fn(async () => 'notices-credential')
    await desktopListenForNotices(7, issue)
    expect(issue).not.toHaveBeenCalled()
    expect(invoke).not.toHaveBeenCalled()
  })

  it('has the app stop on sign-out', () => {
    const invoke = desktopApp(app)
    desktopStopNotices()
    expect(invoke).toHaveBeenCalledWith('stop_notices')
  })
})

describe('authorizing from the app', () => {
  const app = { origin: 'https://okcheese.com', theme: 'system', titleBar: 'native', can: ['links'] }

  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('goes to the browser, and the page stays where it is', () => {
    desktopApp(app)
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const before = window.location.href
    expect(goAuthorize('https://github.com/login/oauth/authorize?state=s')).toBe(true)
    expect(open).toHaveBeenCalledWith('https://github.com/login/oauth/authorize?state=s', '_blank')
    expect(window.location.href).toBe(before)
  })

  it('gives the browser only the hash of the secret the app keeps', async () => {
    desktopApp(app)
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const url = await signInInBrowser('signin', '/inbox')
    const opened = new URL(String(open.mock.calls[0][0]), 'https://okcheese.com')
    expect(opened.href).toBe(url)
    expect(opened.pathname).toBe('/account/oauth/app')
    expect(opened.searchParams.get('entry')).toBe('signin')
    const kept = takeSignInVerifier()
    expect(kept?.target).toBe('/inbox')
    expect(opened.href).not.toContain(kept!.verifier)
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(kept!.verifier)))
    const hash = btoa(String.fromCharCode(...digest))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    expect(opened.searchParams.get('challenge')).toBe(hash)
  })

  it("carries a friend's invite code to the browser, and nothing when there is none", async () => {
    desktopApp(app)
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    await signInInBrowser('signup', '/', 'K7Q2M9')
    expect(new URL(String(open.mock.calls[0][0])).searchParams.get('invite')).toBe('K7Q2M9')
    await signInInBrowser('signup', '/')
    expect(new URL(String(open.mock.calls[1][0])).searchParams.has('invite')).toBe(false)
  })

  it('uses the secret at most once', async () => {
    desktopApp(app)
    vi.spyOn(window, 'open').mockReturnValue(null)
    await signInInBrowser('signup', '/')
    expect(takeSignInVerifier()).not.toBeNull()
    expect(takeSignInVerifier()).toBeNull()
  })
})
