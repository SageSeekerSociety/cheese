/** 频道里的任务卡：等的是看的人自己时对他说，等别人时说出是谁；还开着的任务说出采纳过几步。 */
import type { ChannelTask } from './channelTasks'

import { describe, expect, it } from 'vitest'

import { taskLine, tasksByOrigin } from './channelTasks'

import { setLocale } from '@/i18n'

setLocale('zh-CN')

const NAMES: Record<string, string> = { linxiao: '林晓', chenmo: '陈默' }
const nameOf = (handle: string) => NAMES[handle] ?? handle

function task(over: Partial<ChannelTask> = {}): ChannelTask {
  return {
    id: 't1',
    room_id: 'r1',
    title: '表单字段精简',
    status: 'open',
    owner_handle: 'chenmo',
    created_at: '2026-10-07T01:00:00Z',
    updated_at: '2026-10-07T01:00:00Z',
    presentation: { column: 'needs_you', phrase: 'awaiting_review' },
    waiting_on: 'linxiao',
    ...over,
  } as ChannelTask
}

describe('任务卡', () => {
  it('只是关注结果的人不被写成等你回答', () => {
    const line = taskLine(
      task({
        presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
        awaits_me: true,
        needs_my_action: false,
      }),
      'chenmo',
      nameOf,
      '芝士'
    )
    expect(line.mine).toBe(false)
    expect(line.state.line).toContain('林晓')
    expect(line.state.line).not.toContain('等你')
  })

  it('多人候答时，当前被问者仍有行动强调', () => {
    const line = taskLine(
      task({
        presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
        waiting_on: null,
        awaits_me: true,
        needs_my_action: true,
      }),
      'linxiao',
      nameOf,
      '芝士'
    )
    expect(line.mine).toBe(true)
    expect(line.state.line).toContain('等你')
  })

  it('等的是看的人自己，就对他说；别人看到的是在等谁', () => {
    const mine = taskLine(task({ needs_my_action: true }), 'linxiao', nameOf, '芝士')
    const theirs = taskLine(task({ needs_my_action: false }), 'chenmo', nameOf, '芝士')
    expect(mine.mine).toBe(true)
    expect(mine.state.line).toContain('等你')
    expect(mine.state.line).not.toContain('林晓')
    expect(theirs.mine).toBe(false)
    expect(theirs.state.line).toContain('林晓')
  })

  it('最后一步采纳后写出合进去的是哪个 PR', () => {
    const line = taskLine(
      task({
        status: 'closed',
        presentation: { column: 'done', phrase: 'accepted' },
        waiting_on: null,
        accepted_count: 2,
        last_accepted_pr: 46,
      }),
      'linxiao',
      nameOf,
      '芝士'
    )
    expect(line.state.kind).toBe('closed')
    expect(line.state.label).toContain('46')
    expect(line.accepted).toBeNull()
  })

  it('还开着的任务采纳过几步，就说采纳过几次', () => {
    const line = taskLine(
      task({ presentation: { column: 'building', phrase: 'running' }, waiting_on: null, accepted_count: 1 }),
      'linxiao',
      nameOf,
      '芝士'
    )
    expect(line.state.kind).toBe('running')
    expect(line.accepted).toContain('1')
  })

  it('交付被退回了，是红灯（不是等你）：球已经交回给队友去改', () => {
    const line = taskLine(
      task({ presentation: { column: 'needs_you', phrase: 'bounced' }, waiting_on: null }),
      'linxiao',
      nameOf,
      '芝士'
    )
    expect(line.state.tone).toBe('danger')
  })

  it('一条消息下面挂着从它出来的每一件任务，按创建的先后', () => {
    const under = tasksByOrigin([
      task({ id: 'b', upgraded_from_block_id: 'm1', created_at: '2026-10-07T02:00:00Z' }),
      task({ id: 'a', upgraded_from_block_id: 'm1', created_at: '2026-10-07T01:00:00Z' }),
      task({ id: 'c', upgraded_from_block_id: null }),
    ])
    expect(under.get('m1')?.map((t) => t.id)).toEqual(['a', 'b'])
    expect([...under.keys()]).toEqual(['m1'])
  })
})
