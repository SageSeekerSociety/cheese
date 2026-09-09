import { watch } from 'vue'
import { createI18n } from 'vue-i18n'

import websiteEnglish from './messages/en/website.json'
import zhCN from './messages/zh-CN'
import websiteChinese from './messages/zh-CN/website.json'

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

const i18n = createI18n({
  legacy: false,
  locale: resolveInitialLocale(),
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': { ...zhCN, website: websiteChinese },
    en: { website: websiteEnglish },
  },
})

export const { t } = i18n.global

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
