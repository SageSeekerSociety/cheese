import type { Block } from '../../../cx_types'

import { describe, expect, it } from 'vitest'

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
