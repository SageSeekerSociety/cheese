/** 点任务页顶上那句话，去的是那句话所在的那段对话，并停在那一条上。 */
import type { TaskSource } from '@/types/taskOrigin'

import { describe, expect, it } from 'vitest'

import { sourceRoute } from './taskSource'

function source(where: TaskSource['where'], conversation: string): TaskSource {
  return {
    block_id: 'b1',
    author: 'andy',
    content: '修一下',
    created_at: '2026-10-11T21:54:17Z',
    conversation_id: conversation,
    room_id: 'r1',
    where,
    title: 'bug-fixing',
  }
}

describe('sourceRoute', () => {
  it('opens a channel message in the channel, at that message', () => {
    expect(sourceRoute('p1', source('channel', 'r1'))).toEqual({
      name: 'workspace-topic',
      params: { projectId: 'p1', topicId: 'r1' },
      query: { block: 'b1' },
    })
  })

  it('opens a reply in its 支线, at that reply', () => {
    expect(sourceRoute('p1', source('thread', 'th1'))).toEqual({
      name: 'workspace-thread',
      params: { projectId: 'p1', topicId: 'r1', threadId: 'th1' },
      query: { block: 'b1' },
    })
  })

  it('opens a message said inside another task in that task, at that message', () => {
    expect(sourceRoute('p1', source('task', 't2'))).toEqual({
      name: 'workspace-task',
      params: { projectId: 'p1', topicId: 'r1', taskId: 't2' },
      query: { block: 'b1' },
    })
  })
})
