import type { Block } from '@/cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DAY_MS,
  dayKey,
  dayLabel,
  dayLabelsFor,
  outboxEdgeAfter,
  REGROUP_GAP_MS,
  runEdgeBetween,
  sameSpeaker,
  unreadAnchorBlock,
} from './chatGrouping'

import { setLocale } from '@/i18n'

// These assertions read the Chinese copy.
beforeEach(() => {
  setLocale('zh-CN')
})

/** A block at local noon `daysAgo` days back: noon so a DST shift cannot move it. */
function atDaysAgo(daysAgo: number, minutes = 0): string {
  const d = new Date()
  d.setDate(d.getDate() - daysAgo)
  d.setHours(12, minutes, 0, 0)
  return d.toISOString()
}

const block = (id: string, extra: Partial<Block> = {}): Block =>
  ({
    id,
    kind: 'message',
    author: 'me',
    author_type: 'participant',
    created_at: atDaysAgo(0),
    ...extra,
  }) as Block

const row = (id: string, extra: Partial<Block> = {}) => ({ block: block(id, extra) })

describe('dayKey', () => {
  it('is the same key for two times on the same local day', () => {
    expect(dayKey(atDaysAgo(3, 5))).toBe(dayKey(atDaysAgo(3, 500)))
  })

  it('changes when the local day changes', () => {
    expect(dayKey(atDaysAgo(0))).not.toBe(dayKey(atDaysAgo(1)))
  })

  it('separates days less than 24h apart across midnight', () => {
    // 23:50 and 00:10 the next day are 20 minutes apart, and still two days.
    const late = new Date()
    late.setHours(23, 50, 0, 0)
    const early = new Date(late.getTime() + 20 * 60 * 1000)
    expect(dayKey(late.toISOString())).not.toBe(dayKey(early.toISOString()))
  })
})

describe('dayLabel', () => {
  it('names today and yesterday', () => {
    expect(dayLabel(atDaysAgo(0))).toBe('今天')
    expect(dayLabel(atDaysAgo(1))).toBe('昨天')
  })

  it('names the weekday inside the last week', () => {
    const d = new Date()
    d.setDate(d.getDate() - 3)
    d.setHours(12, 0, 0, 0)
    expect(dayLabel(d.toISOString())).toBe(['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()])
  })

  it('falls back to a date once the week is out', () => {
    const label = dayLabel(atDaysAgo(30))
    expect(label).not.toBe('今天')
    expect(label).not.toBe('昨天')
    expect(DAY_MS).toBe(86_400_000)
  })
})

describe('dayLabelsFor', () => {
  it('labels the first row', () => {
    const labels = dayLabelsFor([row('a'), row('b')])
    expect(labels.get('a')).toBe('今天')
    expect(labels.has('b')).toBe(false)
  })

  it('labels the row the day changes on', () => {
    const labels = dayLabelsFor([row('a', { created_at: atDaysAgo(1) }), row('b'), row('c')])
    expect(labels.get('a')).toBe('昨天')
    expect(labels.get('b')).toBe('今天')
    expect(labels.has('c')).toBe(false)
  })

  it('labels nothing when there is nothing to draw', () => {
    expect(dayLabelsFor([]).size).toBe(0)
  })
})

describe('sameSpeaker', () => {
  it('goes by handle and identity, not by name', () => {
    expect(sameSpeaker(block('a'), block('b'))).toBe(true)
    expect(sameSpeaker(block('a'), block('b', { author: 'you' }))).toBe(false)
    expect(sameSpeaker(block('a'), block('b', { author_type: 'platform' }))).toBe(false)
  })
})

describe('runEdgeBetween', () => {
  it('starts a run at the first row', () => {
    expect(runEdgeBetween(undefined, block('a'), { broken: false })).toBe('start')
  })

  it('continues the run for the same speaker in the same day', () => {
    const prev = block('a')
    const cur = block('b', { created_at: new Date(Date.parse(prev.created_at) + 60_000).toISOString() })
    expect(runEdgeBetween(prev, cur, { broken: false })).toBe('cont')
  })

  it('regroups when the same speaker comes back an hour later', () => {
    const prev = block('a')
    const cur = block('b', { created_at: new Date(Date.parse(prev.created_at) + REGROUP_GAP_MS).toISOString() })
    expect(runEdgeBetween(prev, cur, { broken: false })).toBe('regroup')
  })

  it('starts a run when somebody else speaks', () => {
    expect(runEdgeBetween(block('a'), block('b', { author: 'you' }), { broken: false })).toBe('start')
  })

  it('starts a run on a new day', () => {
    expect(runEdgeBetween(block('a', { created_at: atDaysAgo(1) }), block('b'), { broken: false })).toBe('start')
  })

  it('starts a run around an event row', () => {
    // An event is a hard boundary: a message under it is not a continuation of
    // whatever the same person said above the event.
    expect(runEdgeBetween(block('a'), block('b', { kind: 'event' }), { broken: false })).toBe('start')
    expect(runEdgeBetween(block('a', { kind: 'event' }), block('b'), { broken: false })).toBe('start')
  })

  it('starts a run under a marker the caller says broke it', () => {
    // 「已派出」标记 / 新消息线 插在中间：下面这条必须重新带上名字。
    expect(runEdgeBetween(block('a'), block('b'), { broken: true })).toBe('start')
  })
})

describe('outboxEdgeAfter', () => {
  it('starts when there is nothing above it', () => {
    expect(outboxEdgeAfter(undefined, { mine: true })).toBe('start')
  })

  it('starts under an event or under a broken run', () => {
    expect(outboxEdgeAfter(block('a', { kind: 'event' }), { mine: true })).toBe('start')
  })

  it('starts when the last message is somebody else s', () => {
    expect(outboxEdgeAfter(block('a'), { mine: false })).toBe('start')
  })

  it('continues the run right after something just said', () => {
    const last = block('a', { created_at: new Date(Date.now() - 60_000).toISOString() })
    expect(outboxEdgeAfter(last, { mine: true })).toBe('cont')
  })

  it('regroups when the last message is old, and starts on another day', () => {
    // 「一个间隔以前」要和现在同一天：钉在中午，否则零点后一小时内它落到昨天，
    // 测的就成了「换了一天」。
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      const noon = new Date()
      noon.setHours(12, 0, 0, 0)
      vi.setSystemTime(noon)
      const old = new Date(Date.now() - REGROUP_GAP_MS).toISOString()
      expect(outboxEdgeAfter(block('a', { created_at: old }), { mine: true })).toBe('regroup')
      expect(outboxEdgeAfter(block('a', { created_at: atDaysAgo(1) }), { mine: true })).toBe('start')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('unreadAnchorBlock', () => {
  const rows = [
    row('m1', { author: 'you' }),
    row('m2', { author: 'me' }),
    row('e1', { kind: 'event', author: 'you' }),
    row('m3', { author: 'you' }),
    row('m4', { author: 'you' }),
  ]

  it('draws no line when nothing was unread', () => {
    expect(unreadAnchorBlock(rows, 0, 'me')).toBeNull()
    expect(unreadAnchorBlock(rows, -1, 'me')).toBeNull()
  })

  it('counts back from the newest, other people s messages only', () => {
    // The event row is not a message, and 'me' is not somebody else.
    expect(unreadAnchorBlock(rows, 1, 'me')).toBe('m4')
    expect(unreadAnchorBlock(rows, 3, 'me')).toBe('m1')
  })

  it('falls back to the oldest message in the page when the count is larger', () => {
    expect(unreadAnchorBlock(rows, 99, 'me')).toBe('m1')
  })

  it('is null when this page holds nothing of somebody else s', () => {
    expect(unreadAnchorBlock([row('m1'), row('e1', { kind: 'event' })], 3, 'me')).toBeNull()
  })
})
