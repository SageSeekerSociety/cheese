import { watch } from 'vue'
import { createI18n } from 'vue-i18n'

import en from './messages/en'
import zhCN from './messages/zh-CN'

export type Locale = 'zh-CN' | 'en'
// Signed in, the account's language is the choice (`services/account.ts`) and
// this is its copy in the browser, read before the account is. Signed out, it is
// the only place a choice is kept.
const preferenceKey = 'cheese:locale'

export function isLocale(value: unknown): value is Locale {
  return value === 'en' || value === 'zh-CN'
}

/** The language this browser keeps, or null when it was never picked here and
 *  the page only follows the browser's own language. */
export function storedLocale(): Locale | null {
  try {
    const saved = localStorage.getItem(preferenceKey)
    if (isLocale(saved)) return saved
  } catch {
    // Browser storage can be unavailable in private or restricted contexts.
  }
  return null
}

export function resolveInitialLocale(): Locale {
  const saved = storedLocale()
  if (saved) return saved
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

// A key missing from the active locale falls back to `zh-CN` so a half-translated
// screen degrades to readable text rather than to the raw key. That fallback is
// silent at runtime by design — the guarantee that no key is missing English
// lives in `catalog.spec.ts`, not here. Development builds warn on every missing
// and every fallback so a gap is visible while working.
const i18n = createI18n({
  legacy: false,
  locale: resolveInitialLocale(),
  fallbackLocale: 'zh-CN',
  missingWarn: import.meta.env.DEV,
  fallbackWarn: import.meta.env.DEV,
  messages: { 'zh-CN': zhCN, en },
})

export const { t } = i18n.global

export { LANGUAGE_NAMES, LANGUAGE_SWITCH_LABELS, otherLocale } from './languages'

/** Show `locale` and remember it in this browser. A person picking a language
 *  goes through `chooseLocale`. */
export function setLocale(locale: Locale) {
  i18n.global.locale.value = locale
  try {
    localStorage.setItem(preferenceKey, locale)
  } catch {
    // Switching still works for the current visit without browser storage.
  }
}

const chosen = new Set<(locale: Locale) => void>()

/** A person picked `locale`: show it, and tell whoever keeps the choice — the
 *  account, once signed in (`services/account.ts`). */
export function chooseLocale(locale: Locale) {
  setLocale(locale)
  for (const listener of chosen) listener(locale)
}

export function onLocaleChosen(listener: (locale: Locale) => void) {
  chosen.add(listener)
}

watch(
  i18n.global.locale,
  (locale) => {
    if (typeof document !== 'undefined') document.documentElement.lang = locale
  },
  { immediate: true }
)

export default i18n
