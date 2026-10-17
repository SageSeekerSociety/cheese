import { describe, expect, it } from 'vitest'

import { LCS_MAX_CELLS, lcsOps } from './lcs'
import { lineDiff } from './lineDiff'
import { inlineDiff } from './paragraphDiff'

type Kind = 'same' | 'add' | 'del'

// 旧实现的逐字拷贝（二维表、删优先 / 加优先），用来证明合并后输出一模一样。
function reference(a: string[], b: string[], preferAdd: boolean): { kind: Kind; item: string }[] {
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
  const out: { kind: Kind; item: string }[] = []
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      out.push({ kind: 'same', item: a[i++] })
      j++
    } else if (preferAdd) {
      if (j < b.length && (i >= a.length || lcs[i][j + 1] >= lcs[i + 1][j])) out.push({ kind: 'add', item: b[j++] })
      else out.push({ kind: 'del', item: a[i++] })
    } else if (i < a.length && (j >= b.length || lcs[i + 1][j] >= lcs[i][j + 1])) {
      out.push({ kind: 'del', item: a[i++] })
    } else {
      out.push({ kind: 'add', item: b[j++] })
    }
  }
  return out
}

function rng(seed: number) {
  let s = seed
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

describe('lcsOps', () => {
  it('marks same / add / del in order', () => {
    expect(lcsOps(['a', 'b', 'c'], ['a', 'c', 'd'])).toEqual([
      { kind: 'same', item: 'a' },
      { kind: 'del', item: 'b' },
      { kind: 'same', item: 'c' },
      { kind: 'add', item: 'd' },
    ])
  })

  it('handles empty sides', () => {
    expect(lcsOps([], [])).toEqual([])
    expect(lcsOps(['a'], [])).toEqual([{ kind: 'del', item: 'a' }])
    expect(lcsOps([], ['a'])).toEqual([{ kind: 'add', item: 'a' }])
  })

  it('breaks ties by deleting first, or by adding first when asked', () => {
    expect(lcsOps(['x', 'y'], ['y', 'x']).map((o) => o.kind)).toEqual(['del', 'same', 'add'])
    expect(lcsOps(['x', 'y'], ['y', 'x'], true).map((o) => o.kind)).toEqual(['add', 'same', 'del'])
  })

  it('matches the previous implementations on random inputs', () => {
    const rand = rng(7)
    const pick = () => 'abcd'[Math.floor(rand() * 4)]
    for (let n = 0; n < 1500; n++) {
      const a = Array.from({ length: Math.floor(rand() * 9) }, pick)
      const b = Array.from({ length: Math.floor(rand() * 9) }, pick)
      expect(lcsOps(a, b)).toEqual(reference(a, b, false))
      expect(lcsOps(a, b, true)).toEqual(reference(a, b, true))
    }
  })

  it('degrades to all deleted then all added past the cell limit', () => {
    const side = Math.floor(Math.sqrt(LCS_MAX_CELLS)) + 1
    const a = Array.from({ length: side }, (_, i) => `a${i}`)
    const b = Array.from({ length: side }, (_, i) => `b${i}`)
    const ops = lcsOps(a, b)
    expect(ops).toHaveLength(side * 2)
    expect(ops.slice(0, side).every((o) => o.kind === 'del')).toBe(true)
    expect(ops.slice(side).every((o) => o.kind === 'add')).toBe(true)
  })
})

describe('callers degrade as a whole when too large', () => {
  const side = Math.floor(Math.sqrt(LCS_MAX_CELLS)) + 1
  const a = Array.from({ length: side }, (_, i) => `a${i}`).join('\n')
  const b = Array.from({ length: side }, (_, i) => `b${i}`).join('\n')

  it('lineDiff marks everything removed then added', () => {
    const rows = lineDiff(a, b)
    expect(rows).toHaveLength(side * 2)
    expect(rows.slice(0, side).every((r) => r.kind === 'removed')).toBe(true)
    expect(rows.slice(side).every((r) => r.kind === 'added')).toBe(true)
  })

  it('inlineDiff returns one deleted and one added segment', () => {
    const before = 'x '.repeat(2001)
    const after = 'y '.repeat(2001)
    expect(inlineDiff(before, after)).toEqual([
      { text: before, kind: 'del' },
      { text: after, kind: 'add' },
    ])
  })
})
