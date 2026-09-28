// 公告的显示顺序。这是「置顶」这一批里唯一一条**口径**，公告列表和首页那条横幅
// 都读它，所以出错会同时错在两处 —— 钉住它比钉住任何一页的渲染都要紧。
//
// 三条各自对应一个会被写错的地方：
// 1. 置顶要排在最前，而不是「最近的排最前」；
// 2. 同组（都置顶或都没置顶）按发布时间倒序 —— 不是按 `updatedAt`，改一次就跳位
//    是另一种错法；
// 3. 老数据没有 `pinned` 这一格（加字段之前发出去的公告），要当 `false` 排，不能崩。
import type { SpaceAnnouncement } from '@/types'

import { describe, expect, it } from 'vitest'

import { compareAnnouncements, currentInviteCode, inviteCodeStatus, sortAnnouncements, splitOrigin } from './model'

/** 一条公告。只写关心的字段，其余照真形状给个常数。 */
function announcement(over: Partial<SpaceAnnouncement> & { createdAt: number }): SpaceAnnouncement {
  // 默认值先立成一个变量再展开：写在同一个对象字面量里的话，`createdAt` 会被
  // vue-tsc 判成「写了两次」（TS2783），而它其实只是被 `over` 覆盖。
  const base: SpaceAnnouncement = {
    title: `公告 ${over.createdAt}`,
    content: '<p>正文</p>',
    createdAt: over.createdAt,
    updatedAt: over.createdAt,
    publisher: '蔡松洋',
  }
  return { ...base, ...over }
}

const titles = (list: SpaceAnnouncement[]) => list.map((a) => a.title)

describe('公告的显示顺序', () => {
  it('置顶的排最前 —— 哪怕它是最老的一条', () => {
    const old = announcement({ title: '置顶的老公告', createdAt: 100, pinned: true })
    const newer = announcement({ title: '新的公告', createdAt: 900 })
    const newest = announcement({ title: '更新的公告', createdAt: 1000 })

    expect(titles(sortAnnouncements([newer, newest, old]))).toEqual(['置顶的老公告', '更新的公告', '新的公告'])
  })

  it('同为置顶时，它们之间也按发布时间倒序', () => {
    const a = announcement({ title: '置顶 A', createdAt: 300, pinned: true })
    const b = announcement({ title: '置顶 B', createdAt: 700, pinned: true })
    const c = announcement({ title: '普通', createdAt: 800 })

    expect(titles(sortAnnouncements([a, b, c]))).toEqual(['置顶 B', '置顶 A', '普通'])
  })

  it('没置顶的那一组按发布时间倒序 —— 改过标题的不会因此挪到前面', () => {
    const edited = announcement({ title: '改过的老公告', createdAt: 100, updatedAt: 9_000 })
    const fresh = announcement({ title: '新公告', createdAt: 500 })

    expect(titles(sortAnnouncements([edited, fresh]))).toEqual(['新公告', '改过的老公告'])
  })

  it('老数据没有 pinned 这一格时当 false 排，不崩', () => {
    // `pinned` 缺省（老公告）与显式 `false` 是同一组，两者之间按时间倒序。
    const legacy = announcement({ title: '加字段之前发的', createdAt: 100 })
    const explicit = announcement({ title: '写了 false 的', createdAt: 200, pinned: false })
    const pinned = announcement({ title: '置顶的', createdAt: 50, pinned: true })

    expect(legacy.pinned).toBeUndefined()
    expect(titles(sortAnnouncements([legacy, explicit, pinned]))).toEqual(['置顶的', '写了 false 的', '加字段之前发的'])
  })

  it('排序排的是副本，store 里那份数组的顺序一个字节都不动', () => {
    // 这条不是洁癖：`stores/space.ts` 的 `updateAnnouncement(index, …)` 按下标写回，
    // store 里那份数组一旦被排过，下标就指到别的条目上了。
    const list = [announcement({ title: '第一格', createdAt: 100 }), announcement({ title: '第二格', createdAt: 900 })]

    const ordered = sortAnnouncements(list)

    expect(titles(ordered)).toEqual(['第二格', '第一格'])
    expect(titles(list)).toEqual(['第一格', '第二格'])
    expect(list[0].title).toBe('第一格')
  })

  it('一条都没有时得到空数组（首页横幅靠它整块不出现）', () => {
    expect(sortAnnouncements([])).toEqual([])
  })

  it('判据本身：先看置顶，再看时间', () => {
    const pinnedOld = announcement({ createdAt: 1, pinned: true })
    const pinnedNew = announcement({ createdAt: 2, pinned: true })
    const plain = announcement({ createdAt: 3 })

    expect(compareAnnouncements(pinnedOld, plain)).toBeLessThan(0)
    expect(compareAnnouncements(plain, pinnedOld)).toBeGreaterThan(0)
    expect(compareAnnouncements(pinnedNew, pinnedOld)).toBeLessThan(0)
    // 同一组里时间相同 = 不分先后。
    expect(compareAnnouncements(announcement({ createdAt: 5 }), announcement({ createdAt: 5 }))).toBe(0)
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
