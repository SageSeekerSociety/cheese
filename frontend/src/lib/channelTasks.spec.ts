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
  it('提需求人有关注提醒，但未被问到时不写成待你回答', () => {
    const line = taskLine(
      task({
        presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
        waiting_on: 'linxiao',
        awaits_me: true,
        needs_my_action: false,
      }),
      nameOf
    )
    expect(line.status).not.toContain('你')
    expect(line.status).toContain('林晓')
    expect(line.tone).toBe('waiting')
  })

  it('多人或未点名的题，没有单个等待人名也能对被问者显示个人提示', () => {
    const line = taskLine(
      task({
        presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
        waiting_on: null,
        awaits_me: true,
        needs_my_action: true,
      }),
      nameOf
    )
    expect(line.tone).toBe('mine')
    expect(line.status).toContain('你')
  })

  it('等的是看的人自己，就对他说；别人看到的是在等谁', () => {
    const mine = taskLine(task({ awaits_me: true, needs_my_action: true }), nameOf)
    const theirs = taskLine(task({ awaits_me: false, needs_my_action: false }), nameOf)
    expect(mine.tone).toBe('mine')
    expect(mine.status).not.toContain('林晓')
    expect(theirs.tone).toBe('waiting')
    expect(theirs.status).toContain('林晓')
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
      nameOf
    )
    expect(line.tone).toBe('done')
    expect(line.status).toContain('46')
    expect(line.accepted).toBeNull()
  })

  it('还开着的任务采纳过几步，就说采纳过几次', () => {
    const line = taskLine(
      task({ presentation: { column: 'building', phrase: 'running' }, waiting_on: null, accepted_count: 1 }),
      nameOf
    )
    expect(line.tone).toBe('running')
    expect(line.accepted).toContain('1')
  })

  it('交付被退回了，是红灯（不是等你）：球已经交回给队友去改', () => {
    const line = taskLine(task({ presentation: { column: 'needs_you', phrase: 'bounced' }, waiting_on: null }), nameOf)
    expect(line.tone).toBe('stuck')
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
