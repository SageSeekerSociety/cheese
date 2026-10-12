import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { relTime } from './relTime'

import { setLocale } from '@/i18n'

/** 用本地时间构造：测试跟着跑它的那台机器的时区走，不写死时区。 */
function local(year: number, month: number, day: number, hour: number, minute = 0): number {
  return new Date(year, month - 1, day, hour, minute, 0, 0).getTime()
}

function iso(ms: number): string {
  return new Date(ms).toISOString()
}

/** 采样那一刻：2026-10-11 12:00（本地）。 */
const NOW = local(2026, 10, 11, 12)
const TODAY = local(2026, 10, 11, 9) // 3 小时前
const YESTERDAY = local(2026, 10, 10, 23) // 13 小时前
const DAY_BEFORE = local(2026, 10, 9, 23) // 37 小时前
const DAY_BEFORE_MORNING = local(2026, 10, 9, 9) // 51 小时前，和上一条同一个日历天
const LAST_YEAR = local(2025, 12, 25, 9)

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => {
  vi.useRealTimers()
})

describe('列表行紧凑时间', () => {
  it('一天以内按流逝时长说', () => {
    expect(relTime(iso(NOW - 30_000))).toBe('刚刚')
    expect(relTime(iso(NOW - 5 * 60_000))).toBe('5分钟前')
    expect(relTime(iso(TODAY))).toBe('3小时前')
  })

  it('一天以上按日历天说：13 小时是昨天，37 小时是前天', () => {
    expect(relTime(iso(YESTERDAY))).toBe('昨天')
    expect(relTime(iso(DAY_BEFORE))).toBe('前天')
  })

  it('同一个日历天给同一个答案', () => {
    expect(relTime(iso(DAY_BEFORE_MORNING))).toBe('前天')
    expect(relTime(iso(local(2026, 10, 9, 0, 1)))).toBe('前天')
  })

  it('2–6 天前说几天前', () => {
    expect(relTime(iso(local(2026, 10, 8, 10)))).toBe('3天前')
    expect(relTime(iso(local(2026, 10, 7, 10)))).toBe('4天前')
  })

  it('7 天及以上给日期，跨年带上年份', () => {
    expect(relTime(iso(local(2026, 10, 4, 12)))).toBe('10-04')
    expect(relTime(iso(local(2025, 12, 25, 9)))).toBe('2025-12-25')
  })

  it('英文：日词按英文出，跨年一样带年份', () => {
    setLocale('en')
    expect(relTime(iso(NOW - 5 * 60_000))).toBe('5 minutes ago')
    expect(relTime(iso(YESTERDAY))).toBe('yesterday')
    expect(relTime(iso(DAY_BEFORE))).toBe('2 days ago')
    expect(relTime(iso(local(2026, 10, 4, 12)))).toBe('10-04')
    expect(relTime(iso(LAST_YEAR))).toBe('2025-12-25')
  })

  it('没有值或不是时间，给空串', () => {
    expect(relTime(null)).toBe('')
    expect(relTime(undefined)).toBe('')
    expect(relTime('')).toBe('')
    expect(relTime('不是时间')).toBe('')
  })
})
