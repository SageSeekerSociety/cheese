import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { platformErrorPresentation } from './platformEvents'

function eventBlock(meta: Record<string, unknown> | null, content = '平台事件'): Block {
  return {
    id: 'event-1',
    topic_id: 'topic-1',
    kind: 'event',
    author_type: 'platform',
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
      status: expect.any(String),
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
    expect(out?.status).toBeTruthy()
  })

  it('gives a missing agent image a concrete recovery treatment', () => {
    const out = platformErrorPresentation(
      eventBlock(
        {
          event_type: 'platform_error',
          code: 'runtime_image_missing',
          title: 'Agent 运行组件暂时缺失',
          retryable: true,
        },
        '平台正在重新准备 Agent 运行组件，本轮还没有开始执行。'
      )
    )

    const generic = platformErrorPresentation(
      eventBlock({ event_type: 'platform_error', code: 'node_offline', retryable: true })
    )
    expect(out?.status).not.toBe(generic?.status)
  })

  it('does not turn ordinary system events into incident cards', () => {
    expect(platformErrorPresentation(eventBlock({ action: 'doc' }))).toBeNull()
    expect(platformErrorPresentation(eventBlock(null))).toBeNull()
  })
})
