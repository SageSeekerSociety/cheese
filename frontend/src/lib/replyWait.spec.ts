import { describe, expect, it } from 'vitest'

import { REPLY_STALL_MS, replyStalled } from './replyWait'

describe('replyStalled', () => {
  const now = Date.parse('2026-09-27T08:00:00Z')

  it('没人在等就不亮', () => {
    expect(replyStalled(null, now)).toBe(false)
    expect(replyStalled(undefined, now)).toBe(false)
  })

  it('等得还不够久不亮，满了就亮', () => {
    expect(replyStalled(new Date(now - REPLY_STALL_MS + 1000).toISOString(), now)).toBe(false)
    expect(replyStalled(new Date(now - REPLY_STALL_MS).toISOString(), now)).toBe(true)
  })

  it('阈值是五分钟', () => {
    expect(replyStalled(new Date(now - 4 * 60_000).toISOString(), now)).toBe(false)
    expect(replyStalled(new Date(now - 5 * 60_000).toISOString(), now)).toBe(true)
  })
})
