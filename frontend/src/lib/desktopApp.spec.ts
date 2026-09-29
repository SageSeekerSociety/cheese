import { afterEach, describe, expect, it, vi } from 'vitest'

import { tellDesktopTheme } from './desktopApp'

type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }

// The web app ships before the desktop app updates, so a page may run in an app
// that does not have a command yet. It must not call one such an app lacks.
function desktopApp(about?: object) {
  const invoke = vi.fn(async () => undefined)
  ;(window as AppWindow).__TAURI__ = { core: { invoke } }
  if (about) (window as AppWindow).__CHEESE_APP__ = about
  return invoke
}

afterEach(() => {
  delete (window as AppWindow).__TAURI__
  delete (window as AppWindow).__CHEESE_APP__
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
