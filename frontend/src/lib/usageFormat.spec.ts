import type { UsageStats } from '../cx_types'

import { describe, expect, it } from 'vitest'

import {
  costLabel,
  costNote,
  fmtCompact,
  fmtCost,
  fmtDelta,
  fmtDuration,
  fmtMs,
  fmtNum,
  fmtPercent,
  fmtSI,
} from './usageFormat'

function stats(over: Partial<UsageStats> = {}): UsageStats {
  return {
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cost_usd: 0,
    turns: 0,
    unpriced_tokens: 0,
    ...over,
  }
}

describe('fmtNum', () => {
  it('groups long token counts', () => {
    // The panel showed 2532615017 as one run of digits.
    expect(fmtNum(2532615017)).toBe('2,532,615,017')
  })

  it('leaves small counts alone and survives missing values', () => {
    expect(fmtNum(3)).toBe('3')
    expect(fmtNum(0)).toBe('0')
    expect(fmtNum(undefined as unknown as number)).toBe('0')
  })
})

describe('fmtCost', () => {
  it('scales precision to the magnitude', () => {
    expect(fmtCost(0)).toBe('$0')
    expect(fmtCost(0.0123)).toBe('$0.0123')
    expect(fmtCost(12.3456)).toBe('$12.35')
    expect(fmtCost(1234.5678)).toBe('$1,235')
  })

  it('never flattens a real amount to $0.0000', () => {
    // A fixed 4 decimals printed "$0.0000" for spend that did happen.
    expect(fmtCost(0.00001)).toBe('<$0.0001')
  })
})

describe('costLabel', () => {
  it('shows the price when everything is priced', () => {
    expect(costLabel(stats({ cost_usd: 1.5 }))).toBe('$1.50')
  })

  it('says 未知 for subscription usage instead of $0.0000', () => {
    // 2.28M tokens with no per-token price: the bug printed $0.0000, which
    // reads as "this cost nothing".
    const u = stats({ total_tokens: 2_280_000, unpriced_tokens: 2_280_000 })
    expect(costLabel(u)).toBe('未知')
    expect(costLabel(u)).not.toContain('0.0000')
  })

  it('marks a partly priced total as incomplete', () => {
    expect(costLabel(stats({ cost_usd: 2, unpriced_tokens: 900_000 }))).toBe('$2.00+')
  })
})

describe('costNote', () => {
  it('is empty when the figure is complete', () => {
    expect(costNote(stats({ cost_usd: 2 }))).toBe('')
  })

  it('explains what is missing, with grouped digits', () => {
    expect(costNote(stats({ unpriced_tokens: 2_280_000 }))).toContain('2,280,000')
    expect(costNote(stats({ cost_usd: 2, unpriced_tokens: 2_280_000 }))).toContain('另有')
  })
})

describe('fmtSI', () => {
  it('climbs k/M/G/T and never lands on 1000000.0M', () => {
    // 1e12 used to render as "1000000.0M" through the old shortTokens ladder.
    expect(fmtSI(1e12)).toBe('1.0\u00a0T')
    expect(fmtSI(1e9)).toBe('1.0\u00a0G')
    expect(fmtSI(2_532_615_017)).toBe('2.5\u00a0G')
    expect(fmtSI(2_500_000)).toBe('2.5\u00a0M')
    expect(fmtSI(20_400)).toBe('20\u00a0k')
    expect(fmtSI(999)).toBe('999')
  })

  it('reports an em-dash for unknown, never a fake zero', () => {
    expect(fmtSI(null)).toBe('\u2014')
    expect(fmtSI(undefined)).toBe('\u2014')
  })
})

describe('fmtCompact', () => {
  it('abbreviates only past 1e6 and keeps the grouped form below', () => {
    expect(fmtCompact(2_532_615_017)).toBe('2.5\u00a0G')
    expect(fmtCompact(999_999)).toBe('999,999')
    expect(fmtCompact(0)).toBe('0')
  })
})

describe('fmtPercent', () => {
  it('never rounds a real non-zero rate to 0%', () => {
    // 3.14159e-7 and 0.000001 both used to print "0%".
    expect(fmtPercent(3.14159e-7)).toBe('<0.1%')
    expect(fmtPercent(0.000001)).toBe('<0.1%')
    expect(fmtPercent(0)).toBe('0%')
  })

  it('scales precision to the magnitude', () => {
    expect(fmtPercent(0.05)).toBe('5.0%')
    expect(fmtPercent(0.5)).toBe('50%')
  })
})

describe('fmtMs', () => {
  it('keeps two significant figures below a millisecond', () => {
    expect(fmtMs(0.35)).toBe('0.35\u00a0ms')
    expect(fmtMs(0)).toBe('0\u00a0ms')
  })

  it('steps ms -> s -> min', () => {
    expect(fmtMs(850)).toBe('850\u00a0ms')
    expect(fmtMs(1200)).toBe('1.2\u00a0s')
    expect(fmtMs(120_000)).toBe('2\u00a0min')
    expect(fmtMs(null)).toBe('\u2014')
  })
})

describe('fmtDuration', () => {
  it('is the single seconds ladder', () => {
    expect(fmtDuration(45)).toBe('45\u00a0s')
    expect(fmtDuration(90)).toBe('1\u00a0min')
    expect(fmtDuration(3 * 3600 + 20 * 60)).toBe('3\u00a0h\u00a020\u00a0min')
    expect(fmtDuration(2 * 86400 + 5 * 3600)).toBe('2\u00a0d\u00a05\u00a0h')
    expect(fmtDuration(undefined)).toBe('\u2014')
  })
})

describe('fmtDelta', () => {
  it('prints the direction, one decimal under 10%, integer above', () => {
    expect(fmtDelta(112, 100)).toBe('+12%')
    expect(fmtDelta(96.6, 100)).toBe('-3.4%')
    expect(fmtDelta(100, 100)).toBe('±0%')
    expect(fmtDelta(0, 50)).toBe('-100%')
  })

  it('stays silent when either side is missing or the base is zero', () => {
    // prev=0：除以零没有答案 —— 「+∞%」是鬼话，卡片只报当前值。
    expect(fmtDelta(5, 0)).toBe('')
    expect(fmtDelta(null, 3)).toBe('')
    expect(fmtDelta(3, null)).toBe('')
    expect(fmtDelta(undefined, undefined)).toBe('')
  })
})
