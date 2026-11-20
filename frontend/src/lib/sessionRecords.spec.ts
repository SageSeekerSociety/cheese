// 现场的「概览」不再把平台的自动重试说第三遍：等着的时候输入框上方那一行在说，放弃了
// 对话里有失败那一句。「原始」里每一条都还在。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { overviewRecords } from './sessionRecords'

function record(id: string, eventType: string): Block {
  return {
    id,
    conversation_id: 'c1',
    kind: 'event',
    author_type: 'platform',
    author: 'cheese-a',
    content: id,
    created_at: '2026-11-01T08:00:00Z',
    meta: { event_type: eventType },
    run_record: true,
  } as Block
}

describe('现场概览里的运行记录', () => {
  it('自动重试的排上、开始、放弃不在概览里，其余运行记录照旧', () => {
    const rows = [
      record('retry', 'api_retry'),
      record('wait', 'turn_resume_wait'),
      record('started', 'turn_resume_started'),
      record('stopped', 'turn_resume_stopped'),
      record('queued', 'turn_queued'),
    ]
    expect(overviewRecords(rows).map((b) => b.id)).toEqual(['retry', 'queued'])
  })
})
