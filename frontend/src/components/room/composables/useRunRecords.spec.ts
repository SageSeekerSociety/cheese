import type { Block } from '../../../cx_types'

import { describe, expect, it } from 'vitest'

import { clockTime } from '../../../lib/dateText'

import { useRunRecords } from './useRunRecords'

function queued(id: string, turn: string, seat: string, ahead?: number): Block {
  return {
    id,
    conversation_id: 'c1',
    kind: 'event',
    author_type: 'platform',
    author: seat,
    content: '项目同时运行的轮次已满，本轮正在排队',
    turn_id: turn,
    meta: {
      event_type: 'turn_queued',
      seat,
      ...(ahead ? { i18n: { content: { key: 'turnQueuedBehind', params: { ahead } } } } : {}),
    },
    created_at: '2026-10-06T10:00:00Z',
  } as Block
}

describe('排队中的队友', () => {
  it('在它那一轮开始之前，输入框上方说它在排队、前面还有几个', () => {
    const records = useRunRecords()
    records.receive(queued('r1', 't1', 'cheese-a', 2))
    const [line] = records.waitingLines(() => '芝士')
    expect(line.name).toBe('芝士')
    expect(line.detail).toContain('2')
  })

  it('那一轮一开始就不再说排队', () => {
    const records = useRunRecords()
    records.receive(queued('r1', 't1', 'cheese-a'))
    records.turnBegan('t1')
    expect(records.waitingLines(() => '芝士')).toEqual([])
  })

  it('换到别的对话之后，上一处的排队不跟过来', () => {
    const records = useRunRecords()
    records.receive(queued('r1', 't1', 'cheese-a'))
    records.reset()
    expect(records.waitingLines(() => '芝士')).toEqual([])
  })
})

function resumeLine(id: string, seat: string, dueAt: string, createdAt: string): Block {
  return {
    id,
    conversation_id: 'c1',
    kind: 'event',
    author_type: 'platform',
    author: 'cheese',
    content: '这一轮没完成，约 15:42 自动重试（模型服务限流）',
    turn_id: 't1',
    meta: { event_type: 'turn_resume_wait', seat, due_at: dueAt },
    created_at: createdAt,
  } as Block
}

function said(id: string, author: string, createdAt: string): Block {
  return {
    id,
    conversation_id: 'c1',
    kind: 'message',
    author_type: 'participant',
    author,
    content: '先别动，我看一下',
    meta: {},
    created_at: createdAt,
  } as Block
}

describe('等自动重试的队友', () => {
  it('刷新之后，输入框上方照样说它约几点自动重试：那一句在对话里，不在运行记录里', () => {
    const due = '2026-10-29T07:42:00Z'
    // 刷新：运行记录从空的开始，对话从历史里读回来。
    const records = useRunRecords()
    const [line] = records.waitingLines(() => '芝士', [resumeLine('b1', 'cheese-a', due, '2026-10-29T07:30:00Z')])
    expect(line.name).toBe('芝士')
    expect(line.detail).toContain(clockTime(Date.parse(due)))
  })

  it('之后有人开口，平台就不再自动重试，也不再说它在等', () => {
    const records = useRunRecords()
    const timeline = [
      resumeLine('b1', 'cheese-a', '2026-10-29T07:42:00Z', '2026-10-29T07:30:00Z'),
      said('b2', 'alice', '2026-10-29T07:31:00Z'),
    ]
    expect(records.waitingLines(() => '芝士', timeline)).toEqual([])
  })
})
