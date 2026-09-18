import { watch } from 'vue'
import { createI18n } from 'vue-i18n'

import en from './messages/en'
import zhCN from './messages/zh-CN'

export type Locale = 'zh-CN' | 'en'
const preferenceKey = 'cheese:locale'

export function resolveInitialLocale(): Locale {
  try {
    const saved = localStorage.getItem(preferenceKey)
    if (saved === 'en' || saved === 'zh-CN') return saved
  } catch {
    // Browser storage can be unavailable in private or restricted contexts.
  }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

// A key missing from the active locale falls back to `zh-CN` so a half-translated
// screen degrades to readable text rather than to the raw key. That fallback is
// silent at runtime by design — the guarantee that the gap is *known* lives in
// `untranslated.json` and `catalog.spec.ts`, not here. Development builds warn on
// every missing and every fallback so the gap is visible while working.
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

export function setLocale(locale: Locale) {
  i18n.global.locale.value = locale
  try {
    localStorage.setItem(preferenceKey, locale)
  } catch {
    // Switching still works for the current visit without browser storage.
  }
}

watch(
  i18n.global.locale,
  (locale) => {
    if (typeof document !== 'undefined') document.documentElement.lang = locale
  },
  { immediate: true }
)

export default i18n
