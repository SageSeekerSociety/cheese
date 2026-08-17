/**
 * Tests for the theme runtime (src/theme.ts).
 *
 * These cover the three things that actually break in hand-rolled theme
 * switchers: persisting the RESOLVED theme instead of the PREFERENCE (which
 * silently kills "follow the OS" the first time anyone touches the control),
 * trusting whatever string is in localStorage, and assuming `matchMedia` and
 * `localStorage` are always there.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  applyResolvedTheme,
  readStoredPreference,
  resolveInitialTheme,
  resolvePreference,
  systemPrefersDark,
  THEME_OPTIONS,
  THEME_STORAGE_KEY,
} from './theme'

/** Replace window.matchMedia with one that reports a fixed OS preference. */
function stubSystemTheme(dark: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: dark && query.includes('dark'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }))
  )
}

describe('theme', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.head.innerHTML = ''
  })

  describe('readStoredPreference', () => {
    it('defaults to following the system when nothing is stored', () => {
      expect(readStoredPreference()).toBe('system')
    })

    it.each(['system', 'light', 'dark'] as const)('reads back a stored %s preference', (value) => {
      localStorage.setItem(THEME_STORAGE_KEY, value)
      expect(readStoredPreference()).toBe(value)
    })

    it('ignores a value that is not a known preference', () => {
      // localStorage is user-writable and survives across deploys, so a key
      // left over from an older scheme must not put the app in a bad state.
      localStorage.setItem(THEME_STORAGE_KEY, 'solarized')
      expect(readStoredPreference()).toBe('system')
    })

    it('falls back to system when localStorage throws', () => {
      vi.stubGlobal('localStorage', {
        getItem: () => {
          throw new Error('SecurityError')
        },
      })
      expect(readStoredPreference()).toBe('system')
    })
  })

  describe('resolvePreference', () => {
    it('honours an explicit choice regardless of the OS', () => {
      stubSystemTheme(true)
      expect(resolvePreference('light')).toBe('light')
      stubSystemTheme(false)
      expect(resolvePreference('dark')).toBe('dark')
    })

    it('follows the OS when the preference is system', () => {
      stubSystemTheme(true)
      expect(resolvePreference('system')).toBe('dark')
      stubSystemTheme(false)
      expect(resolvePreference('system')).toBe('light')
    })
  })

  describe('systemPrefersDark', () => {
    it('reports light when matchMedia is unavailable', () => {
      vi.stubGlobal('matchMedia', undefined)
      expect(systemPrefersDark()).toBe(false)
    })

    it('reports light when matchMedia throws', () => {
      vi.stubGlobal(
        'matchMedia',
        vi.fn(() => {
          throw new Error('unsupported')
        })
      )
      expect(systemPrefersDark()).toBe(false)
    })
  })

  describe('resolveInitialTheme', () => {
    it('prefers the stored choice over the OS', () => {
      stubSystemTheme(true)
      localStorage.setItem(THEME_STORAGE_KEY, 'light')
      expect(resolveInitialTheme()).toBe('light')
    })

    it('keeps following the OS after a user selects "system" again', () => {
      // The regression this guards: storing the resolved value ('dark') rather
      // than the preference ('system') passes every other test here and still
      // pins the user forever.
      localStorage.setItem(THEME_STORAGE_KEY, 'system')
      stubSystemTheme(true)
      expect(resolveInitialTheme()).toBe('dark')
      stubSystemTheme(false)
      expect(resolveInitialTheme()).toBe('light')
    })
  })

  describe('applyResolvedTheme', () => {
    it('stamps data-theme on the document element', () => {
      applyResolvedTheme('dark')
      expect(document.documentElement.dataset.theme).toBe('dark')
      applyResolvedTheme('light')
      expect(document.documentElement.dataset.theme).toBe('light')
    })

    it('rewrites the address-bar colour to match the theme', () => {
      const meta = document.createElement('meta')
      meta.setAttribute('name', 'theme-color')
      meta.setAttribute('content', '#F57F17')
      document.head.appendChild(meta)

      applyResolvedTheme('dark')
      // --canvas of the dark theme; an amber address bar over a near-black page
      // is the thing this exists to avoid.
      expect(meta.getAttribute('content')).toBe('#141517')

      applyResolvedTheme('light')
      expect(meta.getAttribute('content')).toBe('#F57F17')
    })

    it('does not throw when the meta tag is absent', () => {
      expect(() => applyResolvedTheme('dark')).not.toThrow()
      expect(document.documentElement.dataset.theme).toBe('dark')
    })
  })

  describe('THEME_OPTIONS', () => {
    it('offers system as a re-selectable option, not just light and dark', () => {
      expect(THEME_OPTIONS.map((option) => option.value)).toEqual(['system', 'light', 'dark'])
    })
  })
})
