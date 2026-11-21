import type { Block } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import { agentFailures, needsRelogin } from './agentFailures'

function block(over: Partial<Block> & { meta?: Record<string, unknown> | null }, at: string): Block {
  return {
    id: at,
    conversation_id: 'room',
    kind: 'message',
    author_type: 'participant',
    author: 'cedar',
    content: '',
    created_at: at,
    ...over,
  } as Block
}

// 失败记录是平台自己写的事件：作者类型 `platform`，`agent_handle` 在 meta 里说清是哪位的那一轮。
const failure = (handle: string | undefined, code: string, at: string) =>
  block(
    {
      kind: 'event',
      author_type: 'platform',
      author: 'system',
      meta: { event_type: 'turn_failed', severity: 'error', code, ...(handle ? { agent_handle: handle } : {}) },
    },
    at
  )
const nameOf = (h: string) => h.toUpperCase()

describe('teammates stopped on a failure nobody came back to', () => {
  it('lists the teammate a failure names, with its code and when it happened', () => {
    const lines = agentFailures([failure('cedar', 'login_expired', '2026-11-17T13:57:00Z')], new Set(), nameOf)
    expect(lines).toEqual([{ handle: 'cedar', name: 'CEDAR', code: 'login_expired', since: '2026-11-17T13:57:00Z' }])
    expect(needsRelogin(lines[0])).toBe(true)
  })

  it('a failure that names nobody is not attributed to anyone', () => {
    expect(agentFailures([failure(undefined, 'model_error', '2026-11-17T13:57:00Z')], new Set(), nameOf)).toEqual([])
  })

  it('the teammate speaking again clears it', () => {
    const lines = agentFailures(
      [failure('cedar', 'model_error', '2026-11-17T13:57:00Z'), block({ author: 'cedar' }, '2026-11-17T14:10:00Z')],
      new Set(),
      nameOf
    )
    expect(lines).toEqual([])
  })

  it('someone else speaking does not', () => {
    const lines = agentFailures(
      [failure('cedar', 'model_error', '2026-11-17T13:57:00Z'), block({ author: 'fir' }, '2026-11-17T14:10:00Z')],
      new Set(),
      nameOf
    )
    expect(lines.map((l) => l.handle)).toEqual(['cedar'])
  })

  it('a teammate working again is not shown as stopped', () => {
    expect(
      agentFailures([failure('cedar', 'login_expired', '2026-11-17T13:57:00Z')], new Set(['cedar']), nameOf)
    ).toEqual([])
  })

  it('a later failure replaces an earlier one, and only an error counts', () => {
    const warn = block(
      {
        kind: 'event',
        author_type: 'platform',
        author: 'system',
        meta: { event_type: 'turn_failed', severity: 'warn', agent_handle: 'fir' },
      },
      '2026-11-17T13:00:00Z'
    )
    const lines = agentFailures(
      [
        failure('cedar', 'model_error', '2026-11-17T13:10:00Z'),
        failure('cedar', 'login_expired', '2026-11-17T13:50:00Z'),
        warn,
      ],
      new Set(),
      nameOf
    )
    expect(lines).toHaveLength(1)
    expect(lines[0].code).toBe('login_expired')
  })

  it('model errors have no relogin action', () => {
    expect(needsRelogin({ code: 'model_error' })).toBe(false)
    expect(needsRelogin({ code: null })).toBe(false)
  })
})
