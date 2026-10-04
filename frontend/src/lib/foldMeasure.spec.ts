/** 长回复折叠的量高批次：同一帧里先全部读、再全部写；同一行一帧只量一次；卸载了就不量。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { cancelMeasure, queueMeasure } from './foldMeasure'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('折叠量高批次', () => {
  it('先读完所有行，再写所有行', async () => {
    const log: string[] = []
    const a = {}
    const b = {}
    queueMeasure(
      a,
      () => log.push('read a'),
      () => log.push('write a')
    )
    queueMeasure(
      b,
      () => log.push('read b'),
      () => log.push('write b')
    )
    expect(log).toEqual([])
    await vi.advanceTimersByTimeAsync(20)
    expect(log).toEqual(['read a', 'read b', 'write a', 'write b'])
  })

  it('同一行一帧里只量最后要的那一次', async () => {
    const owner = {}
    const read = vi.fn(() => 1)
    const first = vi.fn()
    const last = vi.fn()
    queueMeasure(owner, read, first)
    queueMeasure(owner, read, last)
    await vi.advanceTimersByTimeAsync(20)
    expect(read).toHaveBeenCalledTimes(1)
    expect(first).not.toHaveBeenCalled()
    expect(last).toHaveBeenCalledWith(1)
  })

  it('卸载了的行不再量', async () => {
    const owner = {}
    const read = vi.fn()
    queueMeasure(owner, read, () => {})
    cancelMeasure(owner)
    await vi.advanceTimersByTimeAsync(20)
    expect(read).not.toHaveBeenCalled()
  })
})
