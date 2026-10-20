import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createTypist } from './typist'

describe('createTypist', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('一整格一下子到了，也按打字的节奏放出去', () => {
    const shown: string[] = []
    const typist = createTypist((_call, raw) => shown.push(raw), false)
    typist.feed('c1', 'x'.repeat(90))

    expect(shown).toEqual([])
    vi.advanceTimersByTime(200)
    const partway = shown.at(-1)!.length
    expect(partway).toBeGreaterThan(0)
    expect(partway).toBeLessThan(90)

    vi.advanceTimersByTime(2000)
    expect(shown.at(-1)).toHaveLength(90)
  })

  it('攒得多了就加快，不会落后太久', () => {
    const shown: string[] = []
    const typist = createTypist((_call, raw) => shown.push(raw), false)
    typist.feed('c1', 'x'.repeat(3000))

    vi.advanceTimersByTime(1000)
    expect(shown.at(-1)!.length).toBeGreaterThan(1000)
    vi.advanceTimersByTime(7000)
    expect(shown.at(-1)).toHaveLength(3000)
  })

  it('等放完才算 idle', async () => {
    const typist = createTypist(() => undefined, false)
    typist.feed('c1', 'x'.repeat(45))
    let done = false
    void typist.idle().then(() => {
      done = true
    })

    await vi.advanceTimersByTimeAsync(100)
    expect(done).toBe(false)
    await vi.advanceTimersByTimeAsync(1000)
    expect(done).toBe(true)
  })

  it('换了一次调用，上一次的先整段放完', () => {
    const shown: [string, string][] = []
    const typist = createTypist((call, raw) => shown.push([call, raw]), false)
    typist.feed('c1', 'abcdef')
    typist.feed('c2', 'g')

    expect(shown[0]).toEqual(['c1', 'abcdef'])
  })

  it('减少动态效果时到多少放多少', async () => {
    const shown: string[] = []
    const typist = createTypist((_call, raw) => shown.push(raw), true)
    typist.feed('c1', 'abc')

    expect(shown).toEqual(['abc'])
    await expect(typist.idle()).resolves.toBeUndefined()
  })
})
