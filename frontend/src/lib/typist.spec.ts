import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createTypist } from './typist'

import { DRAFT_WRITE_PACE } from '@/lib/taskDraftAgent'

function record() {
  const shown: string[] = []
  const typist = createTypist((_call, raw) => shown.push(raw), DRAFT_WRITE_PACE, false)
  return { shown, typist, last: () => shown.at(-1) ?? '' }
}

describe('createTypist', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('一格开始前停一下，连引号都不放：表单上还是原来的样子', () => {
    const { typist, last } = record()
    typist.feed('c1', '{"draft":41,"name":"Lab 3 缓存模拟器"}')

    vi.advanceTimersByTime(120)
    expect(last()).toBe('{"draft":41,"name":')

    vi.advanceTimersByTime(1500)
    expect(last()).toBe('{"draft":41,"name":"Lab 3 缓存模拟器"}')
  })

  it('短格子按每秒 60 字写，看得到在打字', () => {
    const { typist, last } = record()
    typist.feed('c1', `{"name":"${'x'.repeat(30)}"}`)

    vi.advanceTimersByTime(220 + 200)
    const partway = last().length - '{"name":"'.length
    expect(partway).toBeGreaterThan(0)
    expect(partway).toBeLessThan(30)
  })

  it('详情再长也在 2 秒左右写完', () => {
    const { typist, last } = record()
    const raw = `{"description":"${'字'.repeat(3000)}"}`
    typist.feed('c1', raw)

    vi.advanceTimersByTime(1200)
    expect(last().length).toBeLessThan(raw.length)
    vi.advanceTimersByTime(1400)
    expect(last()).toBe(raw)
  })

  it('要替换的原文一下子放完，换上的那段先停一下再写', () => {
    const { typist, last } = record()
    typist.feed('c1', '{"description_edits":[{"find":"替换策略为 LRU","replace":"替换策略为 LRU，并统计写回"}]}')

    vi.advanceTimersByTime(40 + 220 + 40)
    expect(last()).toContain('"find":"替换策略为 LRU"')
    vi.advanceTimersByTime(80)
    expect(last()).toContain('"replace":')
  })

  it('等放完才算 idle', async () => {
    const { typist } = record()
    typist.feed('c1', `{"name":"${'x'.repeat(40)}"}`)
    let done = false
    void typist.idle().then(() => {
      done = true
    })

    await vi.advanceTimersByTimeAsync(200)
    expect(done).toBe(false)
    await vi.advanceTimersByTimeAsync(2000)
    expect(done).toBe(true)
  })

  it('换了一次调用，上一次的先整段放完', () => {
    const shown: [string, string][] = []
    const typist = createTypist((call, raw) => shown.push([call, raw]), DRAFT_WRITE_PACE, false)
    typist.feed('c1', '{"name":"abcdef"}')
    typist.feed('c2', '{')

    expect(shown[0]).toEqual(['c1', '{"name":"abcdef"}'])
  })

  it('减少动态效果时到多少放多少', async () => {
    const shown: string[] = []
    const typist = createTypist((_call, raw) => shown.push(raw), DRAFT_WRITE_PACE, true)
    typist.feed('c1', '{"name":"abc"}')

    expect(shown).toEqual(['{"name":"abc"}'])
    await expect(typist.idle()).resolves.toBeUndefined()
  })
})
