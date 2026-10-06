// 一轮的时间花在哪：排队、等环境、重试，剩下的是干活。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { turnPhases } from './sitePhases'

const T0 = Date.parse('2026-10-07T08:00:00Z')
const iso = (s: number) => new Date(T0 + s * 1000).toISOString()

function step(s: number): Block {
  return {
    id: `s${s}`,
    conversation_id: 't',
    kind: 'event',
    author_type: 'agent',
    author: 'cheese',
    content: '执行命令',
    turn_id: 'turn',
    created_at: iso(s),
    meta: { tool: 'Bash' },
  } as unknown as Block
}

function record(s: number, eventType: string, meta: Record<string, unknown> = {}): Block {
  return {
    ...step(s),
    id: `r${s}`,
    author_type: 'platform',
    author: 'system',
    meta: { event_type: eventType, ...meta },
  } as unknown as Block
}

const of = (spans: ReturnType<typeof turnPhases>) => Object.fromEntries(spans.map((p) => [p.phase, p.seconds]))

describe('一轮的时间花在哪', () => {
  it('没排队、没重试的一轮全是干活', () => {
    expect(of(turnPhases([step(0), step(30)], T0))).toEqual({ work: 30 })
  })

  it('排队算到这一轮开始为止', () => {
    const spans = turnPhases([record(0, 'turn_queued'), step(125), step(185)], T0 + 120_000)
    expect(of(spans)).toEqual({ queue: 120, work: 65 })
  })

  it('重试算到下一步出现为止', () => {
    const spans = turnPhases([step(0), record(10, 'api_retry', { attempt: 2 }), step(40), step(60)], T0)
    expect(of(spans)).toEqual({ retry: 30, work: 30 })
  })

  it('等环境算到它说连上了为止', () => {
    const spans = turnPhases([record(0, 'device_waiting', { state: 'over', at: iso(20) }), step(25), step(40)], T0)
    expect(of(spans)).toEqual({ environment: 20, work: 20 })
  })

  it('一眨眼的一轮没有条可画', () => {
    expect(turnPhases([step(0)], T0)).toEqual([])
  })
})
