import type { Locale } from './index'

// A language is written in itself, in every UI locale. The switch has to read
// 中文 to the visitor who cannot read the current language, and English to the
// one who cannot read Chinese — so these strings are deliberately the same in
// both catalogs. A value that must not change with the locale is not a
// translation, and keeping two copies of it would only be a chance to drift
// apart; they live here instead.
export const LANGUAGE_NAMES: Record<Locale, string> = {
  'zh-CN': '中文',
  en: 'English',
}

export const LANGUAGE_SWITCH_LABELS: Record<Locale, string> = {
  'zh-CN': '切换到中文',
  en: 'Switch to English',
}

export function otherLocale(locale: string): Locale {
  return locale === 'en' ? 'zh-CN' : 'en'
}
