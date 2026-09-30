// 公告的日期。到期日是人在弹窗里挑的一天，服务端要的是一个时刻：两者之间那一步
// 错了，公告就会在到期日当天早上消失，或者多挂一天。
import { describe, expect, it } from 'vitest'

import {
  announcementDay,
  currentInviteCode,
  dayOfExpiry,
  expiryDay,
  expiryFromDay,
  inviteCodeStatus,
  splitOrigin,
} from './model'

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

// 「哪张码还算数」与「当前用的是哪张」。头部下拉的摘要、成员页「让人进来」、邀请码
// 弹窗三处念的是同一条，所以它错了会同时错在三处 —— 钉这一条比钉任何一页的渲染要紧。
//
// 两张形状都要认：真接口那一版（`maxUses` 是数字，0 = 不限，见 `SpaceInviteCode`）
// 与 `store.ts` 映射过的那一版（`null` = 不限，见 `InviteCode`）。弹窗手里拿的是前者，
// 板里别处拿的是后者。
describe('一张码还算不算数', () => {
  const NOW = 1_800_000_000_000

  it('过期了就是过期了，哪怕还有名额', () => {
    const status = inviteCodeStatus({ maxUses: 10, useCount: 1, expiresAt: NOW - 1 }, NOW)
    expect(status.key).toBe('expired')
  })

  it('刚好到期的这一刻就算了，不是还要再等一毫秒', () => {
    expect(inviteCodeStatus({ maxUses: 10, useCount: 1, expiresAt: NOW }, NOW).key).toBe('expired')
    expect(inviteCodeStatus({ maxUses: 10, useCount: 1, expiresAt: NOW + 1 }, NOW).key).toBe('usable')
  })

  it('名额用完了就是用完了', () => {
    expect(inviteCodeStatus({ maxUses: 3, useCount: 3, expiresAt: null }, NOW).key).toBe('exhausted')
    // 还没用完的还在。
    expect(inviteCodeStatus({ maxUses: 3, useCount: 2, expiresAt: null }, NOW).key).toBe('usable')
  })

  it('不限名额的两种写法都不该被判成用尽', () => {
    // 真接口那一版：0 = 不限。
    expect(inviteCodeStatus({ maxUses: 0, useCount: 500, expiresAt: null }, NOW).key).toBe('usable')
    // `store.ts` 映射过的那一版：null = 不限。
    expect(inviteCodeStatus({ maxUses: null, useCount: 500, expiresAt: null }, NOW).key).toBe('usable')
  })

  it('一张都没有可用的，就一个都不挑（不是「勉强挑第一张」）', () => {
    const exhausted = { maxUses: 1, useCount: 1, expiresAt: null }
    const expired = { maxUses: 10, useCount: 0, expiresAt: NOW - 1 }

    expect(currentInviteCode([exhausted, expired], NOW)).toBeNull()
    expect(currentInviteCode([], NOW)).toBeNull()
  })

  it('当前的那张是第一张还能用的 —— 用尽的、过期的都跳过去', () => {
    const exhausted = { code: 'USED-UP', maxUses: 1, useCount: 1, expiresAt: null }
    const expired = { code: 'OLD', maxUses: 10, useCount: 0, expiresAt: NOW - 1 }
    const live = { code: 'LIVE', maxUses: 10, useCount: 0, expiresAt: null }
    const later = { code: 'LATER', maxUses: 10, useCount: 0, expiresAt: null }

    // 列表按建码时间排，所以这是**最早那张还开着的**，不是最后一张。
    expect(currentInviteCode([exhausted, expired, live, later], NOW)?.code).toBe('LIVE')
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
