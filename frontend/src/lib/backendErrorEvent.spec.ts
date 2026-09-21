import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { backendErrorPresentation } from './backendErrorEvent'

function eventBlock(meta: Record<string, unknown> | null, content = '💥 后端报错：ValueError: nope'): Block {
  return {
    id: 'event-1',
    topic_id: 'topic-1',
    kind: 'event',
    author_type: 'system',
    author: 'backend',
    content,
    meta,
    created_at: '2026-08-12T00:00:00Z',
  }
}

describe('backendErrorPresentation', () => {
  it('keeps the headline on one line and the traceback folded away', () => {
    const out = backendErrorPresentation(
      eventBlock({
        event_type: 'backend_error',
        stack: 'Traceback:\n  File "app/x.py", line 3\nValueError: nope',
        where: 'POST /api/topics/abc/chat',
        request_id: 'rid-1',
      })
    )

    expect(out).not.toBeNull()
    expect(out!.line).not.toContain('\n')
    expect(out!.stack).toContain('ValueError: nope')
    expect(out!.where).toBe('POST /api/topics/abc/chat')
    expect(out!.requestId).toBe('rid-1')
    expect(out!.count).toBe(0)
  })

  it('surfaces the occurrence count on a burst summary', () => {
    const out = backendErrorPresentation(
      eventBlock({ event_type: 'backend_error', summary: true, count: 800, window_s: 300 })
    )
    expect(out!.count).toBe(800)
  })

  it('ignores a count that is not on a summary block', () => {
    const out = backendErrorPresentation(eventBlock({ event_type: 'backend_error', count: 800 }))
    expect(out!.count).toBe(0)
  })

  it('tolerates a report that carried no stack or location', () => {
    const out = backendErrorPresentation(eventBlock({ event_type: 'backend_error' }))
    expect(out!.stack).toBe('')
    expect(out!.where).toBe('')
  })

  it('claims only its own event type', () => {
    expect(backendErrorPresentation(eventBlock({ event_type: 'frontend_error' }))).toBeNull()
    expect(backendErrorPresentation(eventBlock({ action: 'doc' }))).toBeNull()
    expect(backendErrorPresentation(eventBlock(null))).toBeNull()
  })
})
