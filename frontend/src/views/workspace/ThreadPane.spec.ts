/** 打开一条支线就算读过它；一条支线可以转出几件任务，转出来的都列在挂着的那条消息下面。 */
import type { Component } from 'vue'
import type { Block, RoomTask, Topic } from '@/cx_types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const markRead = vi.fn()
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ markRead }) }))
const getThread = vi.fn()
vi.mock('@/api/threads', () => ({ getThread: (id: string) => getThread(id) }))
// 频道里的任务（后端那份）：支线面板只问它挂着的那条消息带着的（`blocks`）。
const roomTasks = ref<RoomTask[]>([])
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listRoomTasks: vi.fn(async (_room: string, opts: { blocks?: string[] } = {}) => {
    const rows = roomTasks.value.filter((task) => (opts.blocks ?? []).includes(task.upgraded_from_block_id ?? ''))
    return { data: rows, total: rows.length }
  }),
}))

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

function task(id: string, origin: string, title = '一件任务'): RoomTask {
  return {
    id,
    room_id: 'r1',
    title,
    status: 'open',
    owner_handle: 'alice',
    upgraded_from_block_id: origin,
    created_at: `2026-10-06T00:00:0${id.length}Z`,
    updated_at: '2026-10-06T00:00:00Z',
    presentation: { column: 'building', phrase: 'running' },
  } as RoomTask
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
  roomTasks.value = []
})

describe('支线', () => {
  it('打开就记成读过', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    mount()
    await waitFor(() => expect(markRead).toHaveBeenCalledWith('th1'))
  })

  it('「转为任务」转的是它挂着的那条消息，已经转过的也还能再转', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    roomTasks.value = [task('task9', 'b1')]
    const { findByTestId, emitted } = mount()
    await fireEvent.click(await findByTestId('thread-to-task'))
    expect(emitted()['to-task']).toEqual([['b1']])
  })

  it('从这里转出的任务都列着，点开去那件任务', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    roomTasks.value = [
      task('task9', 'b1', '表单字段精简'),
      task('task10', 'b1', '学号格式校验'),
      task('x', 'b2', '别处'),
    ]
    const { findAllByTestId, emitted, queryByText } = mount()
    const cards = await findAllByTestId('task-card')
    expect(cards.map((c) => c.getAttribute('data-task-id'))).toEqual(['task9', 'task10'])
    expect(queryByText('别处')).toBeNull()
    await fireEvent.click(cards[1])
    expect(emitted()['open-task']).toEqual([['task10']])
  })

  it('反馈卡接在支线的对话后面', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    const { container } = render(ThreadPane as Component, {
      props: { room: ROOM, threadId: 'th1', members: [], topicList: [], memberNames: { alice: '李安' } },
      global: {
        plugins: [vuetify],
        stubs: {
          MarkdownView: true,
          ChatPanel: { template: '<div><slot name="timeline-end" /></div>' },
          AgentFeedbackCard: { props: ['topicId'], template: '<i class="feedback-probe">{{ topicId }}</i>' },
        },
      },
    })
    await waitFor(() => expect(container.querySelector('.feedback-probe')?.textContent).toBe('th1'))
  })
})
