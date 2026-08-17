import type { UsageStats } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { costLabel, costNote, fmtCost, fmtNum } from './usageFormat'

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
