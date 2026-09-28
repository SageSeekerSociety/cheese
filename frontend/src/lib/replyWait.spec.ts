import { describe, expect, it } from 'vitest'

import { replyStalled, stallReasonText, waitedFor } from './replyWait'

describe('replyStalled', () => {
  const now = Date.parse('2026-09-27T08:00:00Z')
  const ago = (min: number) => new Date(now - min * 60_000).toISOString()

  it('没人在等就不亮', () => {
    expect(replyStalled(null, now)).toBe(false)
    expect(replyStalled(undefined, now)).toBe(false)
  })

  it('一般情况等满五分钟才亮', () => {
    expect(replyStalled(ago(4), now, 'mention')).toBe(false)
    expect(replyStalled(ago(5), now, 'mention')).toBe(true)
    expect(replyStalled(ago(5), now, 'check')).toBe(true)
  })

  it('机器够不着不放宽：多半要人去动，五分钟就该看见', () => {
    expect(replyStalled(ago(5), now, 'device_waiting')).toBe(true)
  })

  it('机器在创建、环境在重建放宽到十五分钟', () => {
    for (const reason of ['machine_provisioning', 'sandbox_rebuilt', 'environment_repaired']) {
      expect(replyStalled(ago(14), now, reason)).toBe(false)
      expect(replyStalled(ago(15), now, reason)).toBe(true)
    }
  })
})

describe('stallReasonText', () => {
  const now = Date.parse('2026-09-28T16:00:00Z')
  const hoursAgo = (h: number) => new Date(now - h * 3_600_000).toISOString()

  it('写出是哪个 PR、已经等了多久', () => {
    const text = stallReasonText({ reason: 'check', since: hoursAgo(4), pr: 1950 }, '芝士', now)
    expect(text).toContain('PR #1950')
    expect(text).toContain('4 小时')
  })

  it('不同原因给不同的说法', () => {
    const texts = [
      'device_waiting',
      'machine_provisioning',
      'sandbox_rebuilt',
      'check',
      'conflict',
      'rejected',
      'gate',
      'mention',
    ].map((reason) => stallReasonText({ reason, since: hoursAgo(1) }, '芝士', now))
    expect(new Set(texts).size).toBe(texts.length)
  })
})

describe('waitedFor', () => {
  const now = Date.parse('2026-09-28T16:00:00Z')
  it('分钟、小时、天三档', () => {
    expect(waitedFor(new Date(now - 7 * 60_000).toISOString(), now)).toBe('7 分钟')
    expect(waitedFor(new Date(now - 3 * 3_600_000).toISOString(), now)).toBe('3 小时')
    expect(waitedFor(new Date(now - 50 * 3_600_000).toISOString(), now)).toBe('2 天')
  })
})
