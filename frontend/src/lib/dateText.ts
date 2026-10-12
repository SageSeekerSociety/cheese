/**
 * 全站日期时间的写法，只此一处。
 *
 * 判断「几天前」一律按**本地日历天**，不按流逝时长：23:00 那条过了 13 小时是
 * 1 天前，过了 37 小时是 2 天前。列表行以前拿秒数比大小，把 37 小时前的也写成
 * 「昨天」，中英都一样——这就是要收掉的那条。
 *
 * 不用 dayjs 的 `fromNow()` 也不用插件：它的阈值是固定的，改不掉这条；而且它
 * 会把全站都写成「N 天前」，跨年也剩不下年份。
 *
 * 绝对时间中英同形，24 小时制（`2026-10-11 11:59`）：这个写法两种语言读起来
 * 都没有歧义（`10/11/2026` 读者分不清哪个月），也只维护一套。
 */
import i18n from '@/i18n'

export const DAY_MS = 86_400_000

/** 本地时区那一天的零点。 */
function startOfLocalDay(ms: number): number {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/**
 * `at` 比 `now` 早几个日历天：0 是同一天，1 是昨天，负数在将来。
 *
 * 先各自归到当天零点再相减，夏令时那天差不到 24 小时，所以取整用 `Math.round`。
 */
export function calendarDaysAgo(at: number, now: number = Date.now()): number {
  return Math.round((startOfLocalDay(now) - startOfLocalDay(at)) / DAY_MS)
}

/** 本地时刻，24 小时制、补零：`09:05`。中英同形。 */
export function clockTime(at: number): string {
  const d = new Date(at)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** `10-09`；不在 `now` 那一年就带上年份：`2025-12-25`。 */
export function absDate(at: number, now: number = Date.now()): string {
  const d = new Date(at)
  const md = `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
  return d.getFullYear() === new Date(now).getFullYear() ? md : `${d.getFullYear()}-${md}`
}

/** `10-09 23:00`，跨年就是 `2025-12-25 09:00`。 */
export function absDateTime(at: number, now: number = Date.now()): string {
  return `${absDate(at, now)} ${clockTime(at)}`
}

/** 一行里塞得下的时间戳：今天的给钟点，更早的给日期。 */
export function dayStamp(at: number, now: number = Date.now()): string {
  return calendarDaysAgo(at, now) <= 0 ? clockTime(at) : absDate(at, now)
}

/**
 * 「昨天 / 前天 / 3 天前」这类日词，按界面语言出（en: yesterday / 2 days ago /
 * 3 days ago）。日词和钟点谁是主语，两种语言都是日词在前。
 */
export function relativeDayWord(days: number, locale: string = i18n.global.locale.value): string {
  return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(-days, 'day')
}

/**
 * 带钟点的日词：2–6 个日历天前，中文说「前天」「3 天前 09:00」；英文没有「前天」，
 * 2 天及以上一律给绝对日期。7 天及以上两种语言都给绝对日期（跨年带年份）。
 *
 * 今天和昨天不在这里：那几个词来自各调用点自己的文案键（「今天 {time}」／
 * 「昨天 {time}」／`Today`／`Yesterday`），只有调用点知道该用哪一套。
 */
export function dayTimeText(at: number, now: number = Date.now()): string {
  const days = calendarDaysAgo(at, now)
  if (days >= 2 && days < 7 && i18n.global.locale.value !== 'en') {
    return `${relativeDayWord(days)} ${clockTime(at)}`
  }
  return absDateTime(at, now)
}
