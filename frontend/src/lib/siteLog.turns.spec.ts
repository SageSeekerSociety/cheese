// 现场按轮分组：一轮里的步骤收成一组，组头写得出几步、多久。
import { describe, expect, it } from 'vitest'

import { formatSpan, groupByTurn, isNarration } from './siteLog'

function block(id: string, turn: string | null, at: string, meta?: Record<string, unknown>) {
  return { id, turn_id: turn, created_at: at, meta: meta ?? { tool: 'bash', arg: 'ls' } }
}

describe('groupByTurn', () => {
  it('collects a turn into one group and counts its steps', () => {
    const turns = groupByTurn([
      block('1', 't1', '2026-09-15T20:28:00Z'),
      block('2', 't1', '2026-09-15T20:28:30Z'),
      block('3', 't1', '2026-09-15T20:29:04Z'),
    ])

    expect(turns).toHaveLength(1)
    expect(turns[0].steps).toBe(3)
    expect(turns[0].startedAt).toBe('2026-09-15T20:28:00Z')
    expect(turns[0].seconds).toBe(64)
  })

  it('starts a new group when the turn changes', () => {
    const turns = groupByTurn([block('1', 't1', '2026-09-15T20:28:00Z'), block('2', 't2', '2026-09-15T20:31:00Z')])

    expect(turns.map((t) => t.key)).toEqual(['t1', 't2'])
  })

  it('never merges two runs of the same turn that are not adjacent', () => {
    // 中间隔着另一轮的两段拼到一起，显示出的是一段从未发生过的连续工作。
    const turns = groupByTurn([
      block('1', 't1', '2026-09-15T20:28:00Z'),
      block('2', 't2', '2026-09-15T20:29:00Z'),
      block('3', 't1', '2026-09-15T20:30:00Z'),
    ])

    expect(turns).toHaveLength(3)
  })

  it('leaves a block with no turn on its own', () => {
    // 旧数据和人写的块不带轮次 id。收进上一组等于声称它们属于那一轮。
    const turns = groupByTurn([
      block('1', 't1', '2026-09-15T20:28:00Z'),
      block('2', null, '2026-09-15T20:28:10Z'),
      block('3', null, '2026-09-15T20:28:20Z'),
    ])

    expect(turns).toHaveLength(3)
  })

  it('does not count what 芝士 said as a step', () => {
    const turns = groupByTurn([
      block('1', 't1', '2026-09-15T20:28:00Z'),
      block('2', 't1', '2026-09-15T20:28:40Z', { progress: true }),
    ])

    expect(turns[0].steps).toBe(1)
  })
})

describe('isNarration', () => {
  it('tells 芝士 speaking apart from a tool call', () => {
    expect(isNarration({ progress: true })).toBe(true)
    expect(isNarration({ tool: 'bash', arg: 'ls' })).toBe(false)
    // 旧数据既没有 tool 也没有 progress —— 按原来的工具行渲染，别改判。
    expect(isNarration(null)).toBe(false)
    expect(isNarration({})).toBe(false)
  })
})

describe('formatSpan', () => {
  it('reads as a duration at every scale', () => {
    expect(formatSpan(38)).toBe('38 秒')
    expect(formatSpan(64)).toBe('1 分 04 秒')
    expect(formatSpan(3900)).toBe('1 小时 05 分')
  })
})
