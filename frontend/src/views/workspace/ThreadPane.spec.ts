/** 打开一条支线就算读过它；挂着的那条消息已经转成任务的，给的是打开那个任务，不再给「转为任务」。 */
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const markRead = vi.fn()
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ markRead }) }))
const getThread = vi.fn()
vi.mock('@/api/threads', () => ({ getThread: (id: string) => getThread(id) }))

import ThreadPane from './ThreadPane.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })
const ROOM = { id: 'r1', project_id: 'p1', title: '综合', kind: 'topic', status: 'active' } as unknown as Topic

function root(over: Partial<Block> = {}): Block {
  return {
    id: 'b1',
    conversation_id: 'r1',
    kind: 'message',
    author_type: 'participant',
    author: 'alice',
    content: '首页要改哪些地方？',
    created_at: '2026-10-06T00:00:00Z',
    ...over,
  }
}

function mount() {
  return render(ThreadPane as Component, {
    props: { room: ROOM, threadId: 'th1', members: [], topicList: [], memberNames: { alice: '李安' } },
    global: { plugins: [vuetify], stubs: { ChatPanel: true, MarkdownView: true } },
  })
}

beforeEach(() => {
  markRead.mockClear()
  getThread.mockReset()
})

describe('支线', () => {
  it('打开就记成读过', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    mount()
    await waitFor(() => expect(markRead).toHaveBeenCalledWith('th1'))
  })

  it('还没转成任务：「转为任务」转的是它挂着的那条消息', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    const { findByTestId, emitted } = mount()
    await fireEvent.click(await findByTestId('thread-to-task'))
    expect(emitted()['to-task']).toEqual([['b1']])
  })

  it('已经转成任务：给的是打开那个任务', async () => {
    getThread.mockResolvedValue({
      id: 'th1',
      room_id: 'r1',
      root_block_id: 'b1',
      reply_count: 2,
      root: root({ upgraded_to_task_id: 'task9' }),
    })
    const { findByTestId, queryByTestId, emitted } = mount()
    await fireEvent.click(await findByTestId('thread-open-task'))
    expect(queryByTestId('thread-to-task')).toBeNull()
    expect(emitted()['open-task']).toEqual([['task9']])
  })
})
