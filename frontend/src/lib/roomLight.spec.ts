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

  it('未完成报错、等队友超时或交付已看完均不亮灯', () => {
    expect(LIGHT_STALL_MS).toBe(5 * 60_000)
    expect(lightOf({ ...quiet, incompleteSince: ago(10) }, NOW)).toBeNull()
    expect(lightOf({ ...quiet, waitStalled: true }, NOW)).toBeNull()
    expect(lightOf({ ...quiet, unseenDelivery: true }, NOW)).toBeNull()
  })

  it('交付被退回、还没人来改，不亮黄灯（球已经交回给队友，不是有事等你）', () => {
    expect(lightOf({ ...quiet, bounced: true }, NOW)).toBeNull()
    expect(lightOf({ ...quiet, bounced: true, awaitsMe: true }, NOW)).toBeNull()
  })

  it('有事等我是黄灯，有人回复了我、我还没处理也是同一盏', () => {
    expect(lightOf({ ...quiet, awaitsMe: true }, NOW)).toEqual({ kind: 'yellow' })
    expect(lightOf({ ...quiet, repliedToMe: true }, NOW)).toEqual({ kind: 'yellow' })
  })

  it('有活在往前走，是呼吸绿（在动）', () => {
    expect(lightOf({ ...quiet, moving: true }, NOW)).toEqual({ kind: 'green', breathe: true })
  })

  it('黄压过呼吸绿：同时有活在走又有事等我，黄更该被看见', () => {
    expect(lightOf({ ...quiet, awaitsMe: true, moving: true }, NOW)).toEqual({ kind: 'yellow' })
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
    expect(lightTitle({ kind: 'green', breathe: true })).toBe('有任务正在进行')
    expect(lightTitle({ kind: 'blue' })).toBe('我负责的')
  })

  it('红灯分三种原因说三句：停下报错没人接手、等队友等太久了、退回没人改', () => {
    expect(lightTitle({ kind: 'red', why: 'unfinished' })).toBe('停下来报错，五分钟没人接手')
    expect(lightTitle({ kind: 'red', why: 'waiting' })).toBe('在等一位队友，等太久了')
    expect(lightTitle({ kind: 'red', why: 'bounced' })).toBe('已退回，还没人来改')
  })
})
