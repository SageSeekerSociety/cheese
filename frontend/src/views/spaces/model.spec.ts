// 公告的日期。到期日是人在弹窗里挑的一天，服务端要的是一个时刻：两者之间那一步
// 错了，公告就会在到期日当天早上消失，或者多挂一天。
import { describe, expect, it } from 'vitest'

import { announcementDay, dayOfExpiry, expiryDay, expiryFromDay, splitOrigin } from './model'

describe('公告的到期日', () => {
  it('到期日当天公告还在，第二天零点起才算到期', () => {
    const expiry = expiryFromDay('2026-10-12')
    expect(expiry).toBeGreaterThan(new Date(2026, 9, 12, 23, 59).getTime())
    expect(expiry).toBeLessThanOrEqual(new Date(2026, 9, 13, 0, 0).getTime())
  })

  it('编辑时日期框里放回的是原来挑的那一天', () => {
    for (const day of ['2026-10-12', '2026-12-31', '2027-01-01', '2028-02-29']) {
      expect(dayOfExpiry(expiryFromDay(day))).toBe(day)
    }
  })

  it('卡片上写的到期日就是挑的那一天', () => {
    const now = new Date(2026, 8, 30).getTime()
    expect(expiryDay(expiryFromDay('2026-10-12'), now)).toEqual({ year: null, month: 10, date: 12 })
  })

  it('不是今年的日期带上年份', () => {
    const now = new Date(2026, 8, 30).getTime()
    expect(announcementDay(new Date(2025, 11, 3).getTime(), now)).toEqual({ year: 2025, month: 12, date: 3 })
  })
})

// 出处：真数据里它不是一列，是简介开头的一段文本（从 PDF 发题那条路写进去的
// `【PDF · 第 N 页】`）。认错的两种方向都贵：认不出来 = 那串机器用的字直接给用户
// 看见；认多了 = 手写的题被扣上一枚假标、正文还少一段。所以两边都钉住。
describe('从简介里认出处', () => {
  it('认得出 PDF 那串前缀，并把标记与正文分开放', () => {
    // 前缀与题干**中间不换行** —— 写它的那条路就是这么拼的，所以这条是真实形状。
    expect(splitOrigin('【PDF · 第 3 页】实现一个缓存')).toEqual({
      origin: 'PDF · 第 3 页',
      summary: '实现一个缓存',
    })
  })

  it('页号是几位就认几位 —— 第 12 页和第 3 页一样', () => {
    expect(splitOrigin('【PDF · 第 12 页】题面')).toEqual({ origin: 'PDF · 第 12 页', summary: '题面' })
  })

  it('手写的题（没有那串前缀）原样返回，没有 origin 这一格', () => {
    const handwritten = '实现一个缓存'
    const out = splitOrigin(handwritten)

    expect(out.summary).toBe(handwritten)
    expect(out.origin).toBeUndefined()
  })

  it('前缀只能在**开头**：正文中间提到它不算出处', () => {
    // 「把这批题从【PDF · 第 2 页】里拆出来」这种描述是真会出现的，它是一句正文。
    const text = '把题目从【PDF · 第 2 页】里拆出来'
    expect(splitOrigin(text)).toEqual({ summary: text })
  })

  it('空简介不崩', () => {
    expect(splitOrigin('')).toEqual({ summary: '' })
  })

  it('前缀后面直接结束（题干是空的）时，正文是空串而不是 undefined', () => {
    expect(splitOrigin('【PDF · 第 1 页】')).toEqual({ origin: 'PDF · 第 1 页', summary: '' })
  })

  it('前缀和题干之间有换行/空格时也摘干净，正文不从空白开头', () => {
    expect(splitOrigin('【PDF · 第 4 页】\n\n  题面')).toEqual({ origin: 'PDF · 第 4 页', summary: '题面' })
  })
})
