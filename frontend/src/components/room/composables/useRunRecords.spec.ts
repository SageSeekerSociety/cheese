import type { Block } from '../../../cx_types'
import type { ResumeWait } from '../../../types/roomSocket'

import { describe, expect, it } from 'vitest'

import { type StreamAt, useRunRecords } from './useRunRecords'

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

const DUE = '2026-10-29T07:42:00Z'

/** 后端进程发出的帧里的第几帧。 */
function at(n: number, stream = 'backend'): StreamAt {
  return { stream, at: n }
}

/** 服务端答 sync 时说的那一项：这位队友还欠着一次自动重试。 */
function owed(seat = 'cheese-a', resume = 'r1'): ResumeWait {
  return { conversation_id: 'c1', seat, resume_id: resume, due_at: DUE, since: '2026-10-29T07:30:00Z' }
}

/** 自动重试的一步，一条运行记录（排上、开始、取消）。 */
function step(event: string, resume = 'r1', seat = 'cheese-a', extra: Record<string, unknown> = {}): Block {
  return {
    id: `${event}-${resume}`,
    conversation_id: 'c1',
    kind: 'event',
    author_type: 'platform',
    author: 'cheese',
    content: '自动重试',
    turn_id: 't1',
    meta: { event_type: event, seat, resume_id: resume, ...extra },
    created_at: '2026-10-29T07:30:00Z',
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
  it('打开页面时服务端说它还欠着一次自动重试，输入框上方就说它在等自动重试，不说几点、从出错时算用时', () => {
    const records = useRunRecords()
    records.resumesNow([owed()], at(1))
    const [line] = records.waitingLines(() => '芝士')
    expect(line.name).toBe('芝士')
    expect(line.kind).toBe('retrying')
    expect(line.detail ?? null).toBeNull()
    expect(line.since).toBe(Date.parse('2026-10-29T07:30:00Z') / 1000)
  })

  it('页面开着时排上的自动重试，当场就说', () => {
    const records = useRunRecords()
    records.receive(step('turn_resume_wait', 'r1', 'cheese-a', { due_at: DUE, reason: 'overloaded' }))
    const [line] = records.waitingLines(() => '芝士')
    expect(line.kind).toBe('retrying')
  })

  it('两位队友都在等，各占一行', () => {
    const records = useRunRecords()
    records.resumesNow([owed('cheese-a', 'r1'), owed('cheese-b', 'r2')], at(1))
    const lines = records.waitingLines((h) => (h === 'cheese-a' ? '芝士' : '火花'))
    expect(lines.map((l) => [l.name, l.kind])).toEqual([
      ['芝士', 'retrying'],
      ['火花', 'retrying'],
    ])
  })

  it('平台更新换掉了运行程序的那一次不是调用模型出错：照「正在工作」说', () => {
    const records = useRunRecords()
    records.resumesNow([{ ...owed(), reason: 'runner_replaced' }], at(1))
    const [line] = records.waitingLines(() => '芝士')
    expect(line.kind).toBe('working')
  })

  it.each(['turn_resume_started', 'turn_resume_stopped'])('%s 之后不再说它在等', (event) => {
    const records = useRunRecords()
    records.resumesNow([owed()], at(1))
    records.receive(step(event))
    expect(records.waitingLines(() => '芝士')).toEqual([])
  })

  it('已经开始了的那一次，晚到的现场不会把它说回来', () => {
    const records = useRunRecords()
    records.receive(step('turn_resume_started'))
    records.resumesNow([owed()], at(1))
    expect(records.waitingLines(() => '芝士')).toEqual([])
  })

  it('服务端说已经不欠了，就不再说它在等', () => {
    const records = useRunRecords()
    records.resumesNow([owed()], at(1))
    records.resumesNow([], at(1))
    expect(records.waitingLines(() => '芝士')).toEqual([])
  })

  // 服务端答 sync 时先读库、再发现场；读完之后排上的那一次，它的运行记录先到页面，
  // 那份读早了的现场里没有它。
  it('排在现场之后的那一次，先到了也不会被那份现场抹掉', () => {
    const records = useRunRecords()
    records.receive(step('turn_resume_wait', 'r1', 'cheese-a', { due_at: DUE }), at(7))
    records.resumesNow([], at(6))
    const [line] = records.waitingLines(() => '芝士')
    expect(line.kind).toBe('retrying')
  })

  // 重连时，补发的旧运行记录在那份现场之后才到：它排在现场之前，现场说不欠了就是不欠了。
  it('重连时补发的旧记录，不管先到后到，都不把现场说不欠的那一次说回来', () => {
    const before = useRunRecords()
    before.receive(step('turn_resume_wait', 'r1', 'cheese-a', { due_at: DUE }), at(3))
    before.resumesNow([], at(6))
    expect(before.waitingLines(() => '芝士')).toEqual([])

    const after = useRunRecords()
    after.resumesNow([], at(6))
    after.receive(step('turn_resume_wait', 'r1', 'cheese-a', { due_at: DUE }), at(3))
    expect(after.waitingLines(() => '芝士')).toEqual([])
  })

  it('后端换了进程，旧进程的号不和新现场比', () => {
    const records = useRunRecords()
    records.receive(step('turn_resume_wait', 'r1', 'cheese-a', { due_at: DUE }), at(50, 'before-restart'))
    records.resumesNow([], at(2))
    expect(records.waitingLines(() => '芝士')).toEqual([])
  })

  it('之后有人开口，平台就不再自动重试，也不再说它在等', () => {
    const records = useRunRecords()
    records.resumesNow([owed()], at(1))
    expect(records.waitingLines(() => '芝士', [said('b2', 'alice', '2026-10-29T07:31:00Z')])).toEqual([])
  })
})
