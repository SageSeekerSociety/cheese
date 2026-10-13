import { describe, expect, it } from 'vitest'

import { retryAfter } from './retryAfter'

describe('retryAfter', () => {
  it('同一刻断开的页面不在同一刻重连，也不比退避等得更久', () => {
    const waits = Array.from({ length: 200 }, () => retryAfter(4_000))

    expect(new Set(waits).size).toBeGreaterThan(50)
    for (const wait of waits) {
      expect(wait).toBeLessThanOrEqual(4_000)
      expect(wait).toBeGreaterThanOrEqual(2_000)
    }
  })
})
