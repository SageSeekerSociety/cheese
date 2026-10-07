// 频道主线上那一行任务说的档，不能和任务列表上的说法相反。
import { describe, expect, it } from 'vitest'

import { progressLevel } from './taskProgress'

describe('progressLevel', () => {
  it('下一步在人手上、又不是等审阅：不说「进行中」', () => {
    for (const phrase of ['checks_failed', 'bounced', 'awaiting_answer'] as const) {
      expect(progressLevel({ column: 'needs_you', phrase })).not.toBe('running')
    }
  })

  it('等审阅还是「待审阅」，在做的还是「进行中」', () => {
    expect(progressLevel({ column: 'needs_you', phrase: 'awaiting_review' })).toBe('review')
    expect(progressLevel({ column: 'building', phrase: 'running' })).toBe('running')
  })
})
