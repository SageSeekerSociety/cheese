import { beforeEach, describe, expect, it } from 'vitest'

import {
  BOARD_COLUMNS,
  boardColumnCounts,
  columnDotClass,
  columnDotStyle,
  columnLabel,
  compareTasks,
  liveBoardTasks,
} from './board'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

function task(over: Partial<Parameters<typeof compareTasks>[0]> = {}) {
  return { id: 'a', created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z', ...over }
}

describe('BOARD_COLUMNS', () => {
  it('板面只并排「还没了结」的四列', () => {
    // 已完成收进底部折叠行，板面留给还没了结的；任务不归档，所以 archived 落不到板上。
    expect(BOARD_COLUMNS.map((c) => c.key)).toEqual(['not_started', 'building', 'delivering', 'needs_you'])
  })

  it('列的先后是「离交付多远」，不跟着数据变', () => {
    const twice = [BOARD_COLUMNS.map((c) => c.key), BOARD_COLUMNS.map((c) => c.key)]
    expect(twice[0]).toEqual(twice[1])
  })
})

describe('columnLabel', () => {
  it('每一列各有名字，包括板面上不出现的那两列', () => {
    // done 在折叠行上、archived 在房间上，两者都要有名字可写。
    expect(columnLabel('not_started')).toBe('未开始')
    expect(columnLabel('building')).toBe('进行中')
    expect(columnLabel('delivering')).toBe('检查中')
    expect(columnLabel('needs_you')).toBe('待处理')
    expect(columnLabel('done')).toBe('已完成')
    expect(columnLabel('archived')).toBe('已归档')
  })
})

describe('columnDotClass', () => {
  it('列色只有一个来源 —— 看板、房间总览、侧栏拿到的是同一个 class', () => {
    expect(columnDotClass('building')).toBe('board-dot--building')
    // needs_you 里的下划线在 CSS 类名里是连字符，三处必须换得一模一样。
    expect(columnDotClass('needs_you')).toBe('board-dot--needs-you')
  })
})

describe('columnDotStyle', () => {
  it('颜色一律走 token —— 写死的颜色在两个主题里必然错一个', () => {
    for (const column of ['building', 'delivering', 'needs_you', 'done', 'archived'] as const) {
      for (const value of Object.values(columnDotStyle(column))) {
        if (value === 'dashed') continue
        expect(value).toMatch(/^var\(--[a-z0-9-]+\)$/)
      }
    }
  })

  it('只有「待处理」是实心暖色 —— 板上唯一该抓眼睛的一列', () => {
    expect(columnDotStyle('needs_you')).toEqual({ borderColor: 'var(--warn)', background: 'var(--warn)' })
    // 其余没有一个用暖色，不然「该谁动」就没法一眼看出来。
    for (const column of ['building', 'delivering', 'archived'] as const) {
      expect(JSON.stringify(columnDotStyle(column))).not.toContain('--warn')
    }
  })

  it('把颜色关掉也读得出来 —— 三列的形状互不相同', () => {
    const shape = (c: 'building' | 'delivering' | 'needs_you') => {
      const s = columnDotStyle(c)
      return `${s.borderStyle ?? 'solid'}/${s.background ?? 'none'}`
    }
    expect(new Set([shape('building'), shape('delivering'), shape('needs_you')]).size).toBe(3)
  })
})

describe('compareTasks', () => {
  it('新动过的排前面', () => {
    const older = task({ id: 'a', updated_at: '2026-08-01T00:00:00Z' })
    const newer = task({ id: 'b', updated_at: '2026-08-02T00:00:00Z' })
    expect(compareTasks(newer, older)).toBeLessThan(0)
    expect(compareTasks(older, newer)).toBeGreaterThan(0)
  })

  it('同一时刻的两条按 id 定序 —— 板不会在两次刷新之间来回跳', () => {
    const x = task({ id: 'aaa' })
    const y = task({ id: 'bbb' })
    expect(compareTasks(x, y)).toBeLessThan(0)
    // 全序：反过来比必须给出相反的答案，不能两边都是 0。
    expect(compareTasks(y, x)).toBeGreaterThan(0)
  })

  it('两次排序给出同一个结果，无论输入顺序', () => {
    const rows = [task({ id: 'c' }), task({ id: 'a' }), task({ id: 'b' })]
    const forward = [...rows].sort(compareTasks).map((r) => r.id)
    const backward = [...rows]
      .reverse()
      .sort(compareTasks)
      .map((r) => r.id)
    expect(forward).toEqual(backward)
    expect(forward).toEqual(['a', 'b', 'c'])
  })

  it('没有 updated_at 时退到 created_at，而不是掉到列表最上面', () => {
    const noUpdate = { id: 'a', created_at: '2026-08-01T00:00:00Z', updated_at: undefined }
    const newer = task({ id: 'b', updated_at: '2026-08-05T00:00:00Z' })
    expect(compareTasks(newer, noUpdate)).toBeLessThan(0)
  })
})

describe('liveBoardTasks / boardColumnCounts', () => {
  const on = (room_id: string, column: 'building' | 'delivering' | 'needs_you' | 'done') => ({
    room_id,
    presentation: { column },
  })

  it('已归档房间里没走完的活不上板，交付过的照算', () => {
    const tasks = [on('live', 'building'), on('gone', 'needs_you'), on('gone', 'done')]
    expect(liveBoardTasks(tasks, new Set(['gone']))).toEqual([on('live', 'building'), on('gone', 'done')])
  })

  it('摘要数的是板面三列：已完成不算，归档房间里没走完的也不算', () => {
    const tasks = [
      on('a', 'building'),
      on('a', 'building'),
      on('a', 'needs_you'),
      on('a', 'done'),
      on('gone', 'delivering'),
    ]
    const counts = boardColumnCounts(tasks, new Set(['gone']))
    expect(Object.fromEntries(counts.map((c) => [c.key, c.count]))).toEqual({ needs_you: 1, building: 2 })
  })

  it('该你动的那一列打头', () => {
    const tasks = [on('a', 'building'), on('a', 'delivering'), on('a', 'needs_you')]
    expect(boardColumnCounts(tasks, new Set()).map((c) => c.key)).toEqual(['needs_you', 'building', 'delivering'])
  })
})
