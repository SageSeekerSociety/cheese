// Compact relative time for list rows: 刚刚 / 5分钟前 / 3小时前 / 昨天 / 4天前
// / 06-12 (en: just now / 5 minutes ago / 3 hours ago / yesterday / 4 days ago
// / 06/12). Deterministic formatting, no library.
import i18n from '@/i18n'

export function relTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const locale = i18n.global.locale.value
  const s = (Date.now() - t) / 1000
  if (s < 60) return locale === 'en' ? 'just now' : '刚刚'
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (s < 3600) return rtf.format(-Math.floor(s / 60), 'minute')
  if (s < 86400) return rtf.format(-Math.floor(s / 3600), 'hour')
  if (s < 172800) return rtf.format(-1, 'day')
  if (s < 604800) return rtf.format(-Math.floor(s / 86400), 'day')
  const d = new Date(t)
  if (locale === 'en') return new Intl.DateTimeFormat(locale, { month: '2-digit', day: '2-digit' }).format(d)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}-${dd}`
}
