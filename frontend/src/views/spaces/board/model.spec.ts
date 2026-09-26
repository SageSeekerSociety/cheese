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

import { compareAnnouncements, sortAnnouncements } from './model'

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
