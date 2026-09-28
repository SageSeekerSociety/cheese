import { describe, expect, it } from 'vitest'

import { replyStalled, stallReasonText } from './replyWait'

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
  it('不同原因给不同的说法', () => {
    const texts = ['device_waiting', 'machine_provisioning', 'sandbox_rebuilt', 'check', 'mention'].map((r) =>
      stallReasonText(r, '芝士')
    )
    expect(new Set(texts).size).toBe(texts.length)
  })
})
