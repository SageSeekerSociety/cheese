/**
 * theme.ts — light/dark theme runtime.
 *
 * Three layers have to agree on which theme is showing, and they are updated
 * from here so they cannot drift apart:
 *
 *   1. `<html data-theme>`      — drives the CSS token block in src/style.css
 *   2. Vuetify's theme          — drives component colours (plugins/vuetify.ts)
 *   3. `<meta name=theme-color>` — drives the mobile browser address bar
 *
 * The user's PREFERENCE ('system' | 'light' | 'dark') is what we persist; the
 * RESOLVED theme ('light' | 'dark') is what we paint. Storing the resolved
 * value instead is the classic bug: someone who never touched the setting gets
 * frozen into whatever their OS happened to be on their first visit, and their
 * OS switching to dark at sunset stops working forever.
 *
 * FIRST PAINT: the inline boot script in index.html calls the same logic
 * (duplicated there, deliberately — see the comment in that file) so that
 * `data-theme` is on <html> before the browser paints anything. Without it a
 * dark-mode user gets a white flash on every cold load, which is the single
 * most complained-about defect in hand-rolled theme switchers.
 */

import { computed, onScopeDispose, readonly, ref, watch } from 'vue'
import { useTheme } from 'vuetify'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

/** Must stay in sync with the key used by the boot script in index.html. */
export const THEME_STORAGE_KEY = 'cheesex.theme'

/**
 * Address-bar colour per theme. Light keeps the amber brand mark it has always
 * had; dark uses --canvas, because an amber address bar above a near-black page
 * is a glare source rather than branding.
 */
const META_THEME_COLOR: Record<ResolvedTheme, string> = {
  light: '#F57F17',
  dark: '#141517',
}

const DARK_QUERY = '(prefers-color-scheme: dark)'

/**
 * Every DOM/storage access below is guarded. This module is imported by
 * plugins/vuetify.ts, which vitest loads in environments where `matchMedia` is
 * absent (jsdom does not implement it) and where `localStorage` can throw
 * outright (Safari private mode). A theme helper must never be the reason a
 * test file or a page fails to load.
 */
function mediaQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  try {
    return window.matchMedia(DARK_QUERY)
  } catch {
    return null
  }
}

/** What the OS is asking for right now. Defaults to light when unknowable. */
export function systemPrefersDark(): boolean {
  return mediaQuery()?.matches ?? false
}

export function readStoredPreference(): ThemePreference {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'system') return raw
  } catch {
    // storage unavailable — fall through to the default
  }
  return 'system'
}

function writeStoredPreference(preference: ThemePreference): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    // storage unavailable — the choice just does not survive a reload
  }
}

export function resolvePreference(preference: ThemePreference): ResolvedTheme {
  if (preference === 'system') return systemPrefersDark() ? 'dark' : 'light'
  return preference
}

/**
 * The theme to boot with. Called by plugins/vuetify.ts at plugin-creation time
 * so Vuetify's very first render already matches what the boot script painted.
 */
export function resolveInitialTheme(): ResolvedTheme {
  return resolvePreference(readStoredPreference())
}

/** Push a resolved theme into the DOM (layers 1 and 3). */
export function applyResolvedTheme(resolved: ResolvedTheme): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.theme = resolved
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', META_THEME_COLOR[resolved])
}

/* ---------------------------------------------------------------------------
   Module-level singleton state.

   `useAppTheme()` is deliberately NOT per-component: a toggle in the top bar
   and a radio group in settings must show the same value, and a per-call
   implementation would also register one OS-change listener per mounting
   component and leak them.
   --------------------------------------------------------------------------- */

const preference = ref<ThemePreference>(readStoredPreference())
const systemDark = ref(systemPrefersDark())
const resolved = computed<ResolvedTheme>(() =>
  preference.value === 'system' ? (systemDark.value ? 'dark' : 'light') : preference.value
)

let listening = false
function listenToSystem(): void {
  if (listening) return
  const query = mediaQuery()
  if (!query) return
  listening = true
  // `addEventListener` on MediaQueryList is unsupported on Safari < 14, which
  // still shows up in the wild; the deprecated addListener is the fallback.
  const onChange = (event: MediaQueryListEvent) => {
    systemDark.value = event.matches
  }
  if (typeof query.addEventListener === 'function') query.addEventListener('change', onChange)
  else query.addListener(onChange)
}

export const THEME_OPTIONS: ReadonlyArray<{ value: ThemePreference; label: string; icon: string }> = [
  { value: 'system', label: '跟随系统', icon: 'mdi-monitor' },
  { value: 'light', label: '浅色', icon: 'mdi-white-balance-sunny' },
  { value: 'dark', label: '深色', icon: 'mdi-weather-night' },
]

/**
 * Read the current theme and change it. Safe to call from any component.
 *
 * Must be called from a setup context — it uses Vuetify's `useTheme()`, which
 * needs the injection context to reach the theme instance.
 */
export function useAppTheme() {
  const vuetifyTheme = useTheme()
  listenToSystem()

  // Keep Vuetify in step with whatever the CSS layer is showing. A watcher
  // rather than a one-shot call: `resolved` also changes when the OS flips
  // while the tab is open, not just when the user picks something.
  const stop = watch(resolved, (next) => {
    applyResolvedTheme(next)
    vuetifyTheme.change(next)
  })
  onScopeDispose(stop)

  // Sync on mount too: another component may have changed the preference while
  // this one was unmounted, and the watcher above only fires on change.
  applyResolvedTheme(resolved.value)
  vuetifyTheme.change(resolved.value)

  function setPreference(next: ThemePreference): void {
    preference.value = next
    writeStoredPreference(next)
  }

  /** Cycle system → light → dark → system. */
  function cyclePreference(): void {
    const order: ThemePreference[] = ['system', 'light', 'dark']
    setPreference(order[(order.indexOf(preference.value) + 1) % order.length])
  }

  return {
    preference: readonly(preference),
    resolved: readonly(resolved),
    isDark: computed(() => resolved.value === 'dark'),
    options: THEME_OPTIONS,
    setPreference,
    cyclePreference,
  }
}
