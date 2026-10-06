// A pie gives every slice its own colour from the document's palette. A palette
// has a last colour, and a slice never borrows another's: past it, the smallest
// slices go together into one 「其他」 that holds their sum.
import { beforeEach, describe, expect, it } from 'vitest'

import { pieSlices } from './chartOption'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

describe('a pie with more slices than colours', () => {
  it('keeps every slice when there are colours enough', () => {
    const slices = pieSlices(['甲', '乙', '丙'], [5, 3, 2], 8)
    expect(slices.map((s) => s.name)).toEqual(['甲', '乙', '丙'])
  })

  it('folds the smallest into one 「其他」 holding their sum, so no two slices share a colour', () => {
    const names = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']
    const values = [10, 1, 9, 8, 7, 6, 5, 4, 2, 3]
    const slices = pieSlices(names, values, 8)
    expect(slices).toHaveLength(8)
    expect(slices.at(-1)?.name).toBe('其他')
    expect(slices.at(-1)?.value).toBe(1 + 2 + 3)
    expect(slices.slice(0, 7).map((s) => s.name)).toEqual(['a', 'c', 'd', 'e', 'f', 'g', 'h'])
    expect(slices.reduce((sum, s) => sum + s.value, 0)).toBe(values.reduce((a, b) => a + b, 0))
  })
})
