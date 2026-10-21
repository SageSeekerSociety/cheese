import { beforeEach, describe, expect, it } from 'vitest'

import { brightest, LIGHT_STALL_MS, lightOf, lightTitle } from './roomLight'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const NOW = Date.parse('2026-10-05T08:00:00Z')
const ago = (min: number) => new Date(NOW - min * 60_000).toISOString()

/** 六条事实的默认值：什么都不成立、也没有没恢复的失败、也没在等谁。 */
const quiet = {
  awaitsMe: false,
  repliedToMe: false,
  moving: false,
  unseenDelivery: false,
  incompleteSince: null as string | null,
  waitStalled: false,
  bounced: false,
}

describe('lightOf —— 每盏灯各自的判据', () => {
  it('什么事都没有的不亮', () => {
    expect(lightOf(quiet, NOW)).toBeNull()
  })

  it('本轮未完成持续超过五分钟，亮红灯', () => {
    const failed = { ...quiet, incompleteSince: ago(5) }
    expect(LIGHT_STALL_MS).toBe(5 * 60_000)
    expect(lightOf(failed, NOW - 1)).toBeNull()
    expect(lightOf(failed, NOW)).toBeNull()
    expect(lightOf(failed, NOW + 1)).toEqual({ kind: 'red', why: 'unfinished' })
  })

  it('未完成超时压过黄绿', () => {
    const failed = { ...quiet, awaitsMe: true, unseenDelivery: true, incompleteSince: ago(6) }
    expect(lightOf(failed, NOW)).toEqual({ kind: 'red', why: 'unfinished' })
  })

  it('房间在等一位队友、等太久了，也是红灯（说的是等的那一件）', () => {
    expect(lightOf({ ...quiet, waitStalled: true }, NOW)).toEqual({ kind: 'red', why: 'waiting' })
  })

  it('等队友的红压过黄：那位队友没回来之前，这一行先红着', () => {
    expect(lightOf({ ...quiet, waitStalled: true, awaitsMe: true }, NOW)).toEqual({ kind: 'red', why: 'waiting' })
  })

  it('交付被退回、还没人来改，是红灯（不是有事等你）', () => {
    expect(lightOf({ ...quiet, bounced: true }, NOW)).toEqual({ kind: 'red', why: 'bounced' })
    // 退回那一刻后端也会把它算成「等你」，但球其实已经交回给队友了：红灯压过黄灯。
    expect(lightOf({ ...quiet, bounced: true, awaitsMe: true }, NOW)).toEqual({ kind: 'red', why: 'bounced' })
  })

  it('失败已恢复、没有失败时间或时间无效时，不保留失败红灯', () => {
    for (const incompleteSince of [null, undefined, 'invalid', ago(-1)]) {
      expect(lightOf({ ...quiet, incompleteSince }, NOW)).toBeNull()
    }
  })

  it('有事等我是黄灯，有人回复了我、我还没处理也是同一盏', () => {
    expect(lightOf({ ...quiet, awaitsMe: true }, NOW)).toEqual({ kind: 'yellow' })
    expect(lightOf({ ...quiet, repliedToMe: true }, NOW)).toEqual({ kind: 'yellow' })
  })

  it('做完了、我还没看过，是长绿（不动）', () => {
    expect(lightOf({ ...quiet, unseenDelivery: true }, NOW)).toEqual({
      kind: 'green',
      breathe: false,
    })
  })

  it('有活在往前走，是呼吸绿（在动）', () => {
    expect(lightOf({ ...quiet, moving: true }, NOW)).toEqual({ kind: 'green', breathe: true })
  })

  it('呼吸绿压过长绿：还在往前走的时候，先说「在走」', () => {
    expect(lightOf({ ...quiet, moving: true, unseenDelivery: true }, NOW)).toEqual({
      kind: 'green',
      breathe: true,
    })
  })

  it('黄压过绿：有没看过的交付、同时又有事等我，黄更该被看见', () => {
    expect(lightOf({ ...quiet, awaitsMe: true, unseenDelivery: true }, NOW)).toEqual({ kind: 'yellow' })
  })

  it('红压过呼吸绿：出了问题时，先亮那盏说问题的灯', () => {
    expect(lightOf({ ...quiet, moving: true, incompleteSince: ago(6) }, NOW)).toEqual({
      kind: 'red',
      why: 'unfinished',
    })
  })
})

describe('brightest —— 收起来的一堆行挑一盏', () => {
  const red = { kind: 'red', why: 'unfinished' } as const
  const yellow = { kind: 'yellow' } as const
  const green = { kind: 'green', breathe: false } as const
  const blue = { kind: 'blue' } as const

  it('一盏都没有就是不亮', () => {
    expect(brightest([])).toBeNull()
    expect(brightest([null, undefined, null])).toBeNull()
  })

  it('红 > 黄 > 绿，与顺序无关', () => {
    expect(brightest([green, yellow, red])).toBe(red)
    expect(brightest([red, green, yellow])).toBe(red)
    expect(brightest([green, yellow])).toBe(yellow)
  })

  it('蓝垫在下面', () => {
    expect(brightest([blue, green])).toBe(green)
    expect(brightest([green, blue])).toBe(green)
    expect(brightest([blue, red])).toBe(red)
  })
})

describe('lightTitle —— 灯上悬停那一句', () => {
  it('每种灯各说各的', () => {
    expect(lightTitle({ kind: 'yellow' })).toBe('有事等你')
    expect(lightTitle({ kind: 'green', breathe: false })).toBe('做完了，你还没看过')
    expect(lightTitle({ kind: 'green', breathe: true })).toBe('有活在往前走')
    expect(lightTitle({ kind: 'blue' })).toBe('我负责的')
  })

  it('红灯分三种原因说三句：停下报错没人接手、等队友等太久了、退回没人改', () => {
    expect(lightTitle({ kind: 'red', why: 'unfinished' })).toBe('停下来报错，五分钟没人接手')
    expect(lightTitle({ kind: 'red', why: 'waiting' })).toBe('在等一位队友，等太久了')
    expect(lightTitle({ kind: 'red', why: 'bounced' })).toBe('已退回，还没人来改')
  })
})
