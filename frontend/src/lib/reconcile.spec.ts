import { describe, expect, it } from 'vitest'

import { reconcile } from './reconcile'

describe('reconcile', () => {
  it('returns the previous value itself when nothing changed', () => {
    const prev = { a: 1, rows: [{ id: 'x', n: 1 }], tags: ['p', 'q'] }
    const next = JSON.parse(JSON.stringify(prev)) as typeof prev
    expect(reconcile(prev, next)).toBe(prev)
  })

  it('keeps unchanged rows and swaps only the changed one', () => {
    const a = { id: 'a', status: 'pending' }
    const b = { id: 'b', status: 'pending' }
    const out = reconcile([a, b], [{ id: 'a', status: 'pending' }, { id: 'b', status: 'accepted' }])
    expect(out[0]).toBe(a)
    expect(out[1]).not.toBe(b)
    expect(out[1]).toEqual({ id: 'b', status: 'accepted' })
  })

  it('pairs rows by id when the order changes', () => {
    const a = { id: 'a', n: 1 }
    const b = { id: 'b', n: 2 }
    const out = reconcile([a, b], [{ id: 'b', n: 2 }, { id: 'a', n: 1 }])
    expect(out[0]).toBe(b)
    expect(out[1]).toBe(a)
  })

  it('takes new rows and drops removed ones', () => {
    const a = { id: 'a' }
    const out = reconcile([a, { id: 'gone' }], [{ id: 'a' }, { id: 'new' }])
    expect(out[0]).toBe(a)
    expect(out.map((r) => r.id)).toEqual(['a', 'new'])
  })

  it('keeps an unchanged nested object inside a changed parent', () => {
    const state = { reasons: ['ci'], head: 'abc' }
    const prev = { id: 'c', note: 'old', merge_state: state }
    const out = reconcile(prev, { id: 'c', note: 'new', merge_state: { reasons: ['ci'], head: 'abc' } })
    expect(out).not.toBe(prev)
    expect(out.merge_state).toBe(state)
  })

  it('notices a removed key', () => {
    const prev: Record<string, number> = { a: 1, b: 2 }
    const out = reconcile(prev, { a: 1 })
    expect(out).toEqual({ a: 1 })
    expect(out).not.toBe(prev)
  })

  it('pairs rows without ids by position', () => {
    const first = { n: 1 }
    const out = reconcile([first, { n: 2 }], [{ n: 1 }, { n: 3 }])
    expect(out[0]).toBe(first)
    expect(out[1]).toEqual({ n: 3 })
  })
})
