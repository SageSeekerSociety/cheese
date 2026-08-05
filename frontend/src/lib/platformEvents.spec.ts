import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { platformErrorPresentation } from './platformEvents'

function eventBlock(meta: Record<string, unknown> | null, content = '平台事件'): Block {
  return {
    id: 'event-1',
    topic_id: 'topic-1',
    kind: 'event',
    author_type: 'system',
    author: 'system',
    content,
    meta,
    created_at: '2026-08-05T00:00:00Z',
  }
}

describe('platformErrorPresentation', () => {
  it('maps storage exhaustion to the recovery treatment', () => {
    const out = platformErrorPresentation(
      eventBlock(
        {
          event_type: 'platform_error',
          code: 'storage_exhausted',
          severity: 'error',
          title: '运行环境存储空间不足',
          retryable: true,
        },
        '这轮因运行环境存储空间不足而暂停，项目文件和已完成的改动都还在。'
      )
    )

    expect(out).toEqual({
      code: 'storage_exhausted',
      title: '运行环境存储空间不足',
      body: '这轮因运行环境存储空间不足而暂停，项目文件和已完成的改动都还在。',
      status: '自动清理中 · 稍后 @芝士重试',
      icon: 'mdi-harddisk-alert',
      retryable: true,
    })
  })

  it('keeps unknown platform codes readable instead of dropping the event', () => {
    const out = platformErrorPresentation(
      eventBlock(
        {
          event_type: 'platform_error',
          code: 'node_offline',
          title: '运行节点暂时离线',
          retryable: true,
        },
        '平台正在重新连接。'
      )
    )

    expect(out?.title).toBe('运行节点暂时离线')
    expect(out?.status).toBe('平台正在恢复 · 稍后可重试')
  })

  it('does not turn ordinary system events into incident cards', () => {
    expect(platformErrorPresentation(eventBlock({ action: 'doc' }))).toBeNull()
    expect(platformErrorPresentation(eventBlock(null))).toBeNull()
  })
})
