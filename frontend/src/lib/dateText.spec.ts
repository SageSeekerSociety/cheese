import { beforeEach, describe, expect, it } from 'vitest'

import { absDate, absDateTime, calendarDaysAgo, clockTime, dayStamp, dayTimeText, relativeDayWord } from './dateText'

import { setLocale } from '@/i18n'

/** 用本地时间构造再取毫秒：测试跟着跑它的那台机器的时区走，不写死时区。 */
function local(year: number, month: number, day: number, hour: number, minute = 0): number {
  return new Date(year, month - 1, day, hour, minute, 0, 0).getTime()
}

/** 采样那一刻：2026-10-11 12:00（本地）。 */
const NOW = local(2026, 10, 11, 12)
const TODAY = local(2026, 10, 11, 9) // 3 小时前
const YESTERDAY = local(2026, 10, 10, 23) // 13 小时前
const DAY_BEFORE = local(2026, 10, 9, 23) // 37 小时前
const LAST_YEAR = local(2025, 12, 25, 9)

describe('日历天判定', () => {
  it('按日历天算，不按流逝时长', () => {
    expect(calendarDaysAgo(TODAY, NOW)).toBe(0)
    expect(calendarDaysAgo(YESTERDAY, NOW)).toBe(1)
    expect(calendarDaysAgo(DAY_BEFORE, NOW)).toBe(2)
    // 同一个日历天，过了 51 小时和过了 37 小时是同一个答案。
    expect(calendarDaysAgo(local(2026, 10, 9, 9), NOW)).toBe(2)
    expect(calendarDaysAgo(local(2026, 10, 12, 1), NOW)).toBe(-1)
    expect(calendarDaysAgo(LAST_YEAR, NOW)).toBeGreaterThan(200)
  })
})

describe('钟点与日期', () => {
  it('钟点是 24 小时制、补零，中英同形', () => {
    expect(clockTime(TODAY)).toBe('09:00')
    expect(clockTime(YESTERDAY)).toBe('23:00')
    expect(clockTime(local(2026, 10, 11, 0, 5))).toBe('00:05')
  })

  it('只显示日期：同年给 MM-DD，跨年带上年份', () => {
    expect(absDate(local(2026, 10, 4, 12), NOW)).toBe('10-04')
    expect(absDate(LAST_YEAR, NOW)).toBe('2025-12-25')
  })

  it('日期加时刻', () => {
    expect(absDateTime(DAY_BEFORE, NOW)).toBe('10-09 23:00')
    expect(absDateTime(LAST_YEAR, NOW)).toBe('2025-12-25 09:00')
  })

  it('一行里塞得下的那个：今天给钟点，更早给日期', () => {
    expect(dayStamp(TODAY, NOW)).toBe('09:00')
    expect(dayStamp(YESTERDAY, NOW)).toBe('10-10')
    expect(dayStamp(LAST_YEAR, NOW)).toBe('2025-12-25')
  })
})

describe('带钟点的日词', () => {
  beforeEach(() => {
    setLocale('zh-CN')
  })

  it('中文：2–6 天前说「前天」「N 天前」', () => {
    expect(dayTimeText(DAY_BEFORE, NOW)).toBe('前天 23:00')
    expect(dayTimeText(local(2026, 10, 8, 10), NOW)).toBe('3天前 10:00')
  })

  it('中文：7 天及以上给日期', () => {
    expect(dayTimeText(local(2026, 10, 4, 12), NOW)).toBe('10-04 12:00')
  })

  // 界面语言现在只有 zh-CN 和 en 两种；判定写成「英文族」是为了以后加 en-GB 这类
  // 变体时不静默掉回中文。
  it('英文：2 天及以上一律给绝对日期，英文没有「前天」', () => {
    setLocale('en')
    expect(dayTimeText(DAY_BEFORE, NOW)).toBe('10-09 23:00')
    expect(dayTimeText(local(2026, 10, 8, 10), NOW)).toBe('10-08 10:00')
  })

  it('给不出时间的给空串，不摆 NaN', () => {
    expect(clockTime(NaN)).toBe('')
    expect(absDate(NaN, NOW)).toBe('')
    expect(absDateTime(NaN, NOW)).toBe('')
    expect(dayStamp(NaN, NOW)).toBe('')
    expect(dayTimeText(NaN, NOW)).toBe('')
  })

  it('跨年带上年份，中英同形', () => {
    expect(dayTimeText(LAST_YEAR, NOW)).toBe('2025-12-25 09:00')
    setLocale('en')
    expect(dayTimeText(LAST_YEAR, NOW)).toBe('2025-12-25 09:00')
  })
})

describe('日词', () => {
  it('按界面语言出', () => {
    expect(relativeDayWord(1, 'zh-CN')).toBe('昨天')
    expect(relativeDayWord(2, 'zh-CN')).toBe('前天')
    expect(relativeDayWord(4, 'zh-CN')).toBe('4天前')
    expect(relativeDayWord(1, 'en')).toBe('yesterday')
    expect(relativeDayWord(2, 'en')).toBe('2 days ago')
  })
})
