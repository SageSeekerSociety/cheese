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

    expect(out?.status).toBe('平台组件恢复中 · 稍后 @芝士重试')
    expect(out?.icon).toBe('mdi-package-variant-closed-remove')
  })

  it('tells people to re-@ 芝士 instead of promising a recovery nobody is doing', () => {
    // 未送达 / 轮次超时：平台这边没有任何"正在恢复"的动作，能救它的只有再 @ 一次。
    // 套用通用的「平台正在恢复」就是在承诺一件没人在做的事。
    const undelivered = platformErrorPresentation(
      eventBlock(
        {
          event_type: 'platform_error',
          code: 'prompt_undelivered',
          title: '消息没送到芝士那边',
          retryable: true,
        },
        '这轮没能开始：消息没送进芝士的会话，她那边一点反应都没有。'
      )
    )
    expect(undelivered?.status).toBe('没送达 · 再 @芝士一次就重开会话')
    expect(undelivered?.status).not.toContain('平台正在恢复')

    const timedOut = platformErrorPresentation(
      eventBlock(
        {
          event_type: 'platform_error',
          code: 'turn_timeout',
          title: '这轮跑到时间上限，被强制结束',
          retryable: true,
        },
        '这轮到了平台的时间上限还没跑完，已被强制结束。'
      )
    )
    expect(timedOut?.status).toBe('已强制结束 · 再 @芝士一次接着做')
    expect(timedOut?.icon).toBe('mdi-timer-alert')
  })

  it('does not turn ordinary system events into incident cards', () => {
    expect(platformErrorPresentation(eventBlock({ action: 'doc' }))).toBeNull()
    expect(platformErrorPresentation(eventBlock(null))).toBeNull()
  })
})
