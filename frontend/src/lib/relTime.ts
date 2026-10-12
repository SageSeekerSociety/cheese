// Compact relative time for list rows: 刚刚 / 5分钟前 / 3小时前 / 昨天 / 前天 /
// 4天前 / 10-04 (en: just now / 5 minutes ago / 3 hours ago / yesterday /
// 2 days ago / 4 days ago / 10-04). Deterministic formatting, no library.
//
// 一天以内按流逝时长说，一天以上按**日历天**说：23:00 那条过了 13 小时是
// 「昨天」，过了 37 小时是「前天」——以前拿秒数比大小，两条都写「昨天」。
import i18n, { t } from '@/i18n'
import { absDate, calendarDaysAgo, relativeDayWord } from '@/lib/dateText'

export function relTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return ''
  const locale = i18n.global.locale.value
  const now = Date.now()
  const days = calendarDaysAgo(at, now)
  // 今天之内按流逝时长说；将来（时钟偏了）也走这一支，秒数是负的，按「刚刚」。
  if (days <= 0) {
    const s = (now - at) / 1000
    if (s < 60) return t('topic.time.justNow')
    const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
    if (s < 3600) return rtf.format(-Math.floor(s / 60), 'minute')
    return rtf.format(-Math.floor(s / 3600), 'hour')
  }
  if (days < 7) return relativeDayWord(days, locale)
  return absDate(at, now)
}
