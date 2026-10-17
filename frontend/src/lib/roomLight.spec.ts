import { beforeEach, describe, expect, it } from 'vitest'

import { brightest, LIGHT_STALL_MS, lightOf, lightTitle } from './roomLight'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const NOW = Date.parse('2026-10-05T08:00:00Z')
const ago = (min: number) => new Date(NOW - min * 60_000).toISOString()

/** 六条事实的默认值：什么都不成立、也没在跑、也没有没恢复的失败。 */
const quiet = {
  awaitsMe: false,
  repliedToMe: false,
  moving: false,
  unseenDelivery: false,
  lastOutputAt: null as string | null,
  incompleteSince: null as string | null,
}

describe('lightOf —— 四盏灯各自的判据', () => {
  it('没开过任务、也没活在跑的不亮', () => {
    expect(lightOf(quiet, NOW)).toBeNull()
  })

  it('有活在往前走就是呼吸绿：在跑的和走在交付链路上的都算', () => {
    expect(lightOf({ ...quiet, moving: true }, NOW)).toEqual({ kind: 'green', breathe: true })
    // 走在交付链路上（等闸门、等 CI、解冲突）的 `moving` 也是 true，画同一盏
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: ago(1) }, NOW)).toEqual({
      kind: 'green',
      breathe: true,
    })
  })

  it('显示在跑、又五分钟没有输出，是红的', () => {
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: ago(5) }, NOW)).toEqual({
      kind: 'red',
      breathe: false,
      why: 'quiet',
    })
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: ago(6) }, NOW)?.kind).toBe('red')
  })

  it('差一分钟不红：没满五分钟是正常的在干活', () => {
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: ago(4) }, NOW)).toEqual({
      kind: 'green',
      breathe: true,
    })
    expect(LIGHT_STALL_MS).toBe(5 * 60_000)
  })

  it('说不出上一次输出是什么时候就不红：不知道不等于卡住', () => {
    // 那个戳本来就可能是 null（这一轮跑在别的进程里），也可能记着一个读不出来的值
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: null }, NOW)).toEqual({
      kind: 'green',
      breathe: true,
    })
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: '不是个时间' }, NOW)).toEqual({
      kind: 'green',
      breathe: true,
    })
  })

  it('没在跑的行不红：红灯说的是「显示在跑却没动静」', () => {
    // 一条早就不跑了的活，上一次输出是半小时前——它不该因此变红
    expect(lightOf({ ...quiet, lastOutputAt: ago(30) }, NOW)).toBeNull()
  })

  it('本轮未完成持续超过五分钟，即使不在跑也亮红灯', () => {
    const failed = { ...quiet, incompleteSince: ago(5) }
    expect(lightOf(failed, NOW - 1)).toBeNull()
    expect(lightOf(failed, NOW)).toBeNull()
    expect(lightOf(failed, NOW + 1)).toEqual({ kind: 'red', breathe: false, why: 'unfinished' })
  })

  it('未完成超时压过黄绿，其他成员的新输出不重置失败计时', () => {
    const failed = {
      ...quiet,
      awaitsMe: true,
      moving: true,
      lastOutputAt: ago(0),
      incompleteSince: ago(6),
    }
    expect(lightOf(failed, NOW)).toEqual({ kind: 'red', breathe: false, why: 'unfinished' })
  })

  it('两种红都说得出是哪一件：在跑却没动静，还是停下报错没人接手', () => {
    // 既有没恢复的失败、又在跑却没动静 —— 说的是更重的那一件（坏了这件事）。
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: ago(9), incompleteSince: ago(6) }, NOW)).toEqual({
      kind: 'red',
      breathe: false,
      why: 'unfinished',
    })
    // 只有「在跑却没动静」时仍是红，但说的不是失败。
    expect(lightOf({ ...quiet, moving: true, lastOutputAt: ago(9) }, NOW)).toEqual({
      kind: 'red',
      breathe: false,
      why: 'quiet',
    })
  })

  it('失败已恢复、没有失败时间或时间无效时，不保留失败红灯', () => {
    for (const incompleteSince of [null, undefined, 'invalid', ago(-1)]) {
      expect(lightOf({ ...quiet, incompleteSince }, NOW)).toBeNull()
    }
  })

  it('有事等我是黄灯', () => {
    expect(lightOf({ ...quiet, awaitsMe: true }, NOW)).toEqual({ kind: 'yellow', breathe: false })
  })

  it('有人回复了我、我还没处理，也是同一盏黄灯', () => {
    expect(lightOf({ ...quiet, repliedToMe: true }, NOW)).toEqual({
      kind: 'yellow',
      breathe: false,
    })
  })

  it('回复我那一条压过绿：同时又有人在跑，还是先让我看见有人回我了', () => {
    expect(lightOf({ ...quiet, repliedToMe: true, moving: true, lastOutputAt: ago(1) }, NOW)).toEqual({
      kind: 'yellow',
      breathe: false,
    })
  })

  it('做完了、我还没看过，是长绿（不闪）', () => {
    expect(lightOf({ ...quiet, unseenDelivery: true }, NOW)).toEqual({ kind: 'green', breathe: false })
  })

  it('红压过黄：既卡住了又有事等我，先让我看见卡住的那一件', () => {
    expect(
      lightOf({ awaitsMe: true, repliedToMe: true, moving: true, unseenDelivery: true, lastOutputAt: ago(9) }, NOW)
    ).toEqual({ kind: 'red', breathe: false, why: 'quiet' })
  })

  it('黄压过绿：在往前走、同时又有事等我，黄更该被看见', () => {
    expect(
      lightOf({ awaitsMe: true, repliedToMe: true, moving: true, unseenDelivery: true, lastOutputAt: ago(1) }, NOW)
    ).toEqual({ kind: 'yellow', breathe: false })
  })

  it('呼吸绿压过长绿：又在跑、又有没看过的交付，画动的那一盏', () => {
    expect(lightOf({ ...quiet, moving: true, unseenDelivery: true }, NOW)).toEqual({
      kind: 'green',
      breathe: true,
    })
  })
})

describe('brightest —— 收起来的一堆行挑一盏', () => {
  const red = { kind: 'red', breathe: false } as const
  const yellow = { kind: 'yellow', breathe: false } as const
  const breathe = { kind: 'green', breathe: true } as const
  const still = { kind: 'green', breathe: false } as const
  const blue = { kind: 'blue', breathe: false } as const

  it('一盏都没有就是不亮', () => {
    expect(brightest([])).toBeNull()
    expect(brightest([null, undefined, null])).toBeNull()
  })

  it('红 > 黄 > 绿，与顺序无关', () => {
    expect(brightest([still, yellow, red, breathe])).toBe(red)
    expect(brightest([red, still, yellow, breathe])).toBe(red)
    expect(brightest([still, breathe, yellow])).toBe(yellow)
  })

  it('同一种颜色里，动的那一盏压过不动的', () => {
    expect(brightest([still, breathe])).toBe(breathe)
    expect(brightest([breathe, still])).toBe(breathe)
  })

  it('蓝垫在四盏下面', () => {
    expect(brightest([blue, still])).toBe(still)
    expect(brightest([still, blue])).toBe(still)
    expect(brightest([blue, red])).toBe(red)
    expect(brightest([blue, breathe, yellow])).toBe(yellow)
  })
})

describe('lightTitle —— 灯上悬停那一句', () => {
  it('四种灯各说各的', () => {
    expect(lightTitle({ kind: 'red', breathe: false })).toBe('显示在跑，但五分钟没动静')
    expect(lightTitle({ kind: 'yellow', breathe: false })).toBe('有事等你')
    expect(lightTitle({ kind: 'green', breathe: true })).toBe('有活在往前走')
    expect(lightTitle({ kind: 'green', breathe: false })).toBe('做完了，你还没看过')
    expect(lightTitle({ kind: 'blue', breathe: false })).toBe('我负责的')
  })

  it('红灯分两种原因说两句：在跑却没动静，和停下报错没人接手', () => {
    // 「停下报错」和「显示在跑」说的不是一件事：前者不在跑。两种各有一句。
    expect(lightTitle({ kind: 'red', breathe: false, why: 'quiet' })).toBe('显示在跑，但五分钟没动静')
    expect(lightTitle({ kind: 'red', breathe: false, why: 'unfinished' })).toBe('停下来报错，五分钟没人接手')
  })
})
