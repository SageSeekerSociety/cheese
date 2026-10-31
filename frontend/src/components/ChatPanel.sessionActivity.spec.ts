import type { Block, Topic, TopicProgress, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const listTopicMembers = vi.fn()

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopicMembers: (...args: unknown[]) => listTopicMembers(...args),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...args: unknown[]) => listBlocks(...args),
  }
})

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { clockTime } from '@/lib/dateText'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { loadMarkdownReader } from '@/test/markdownReader'

const topic: Topic = {
  id: 'session-activity-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Session activity',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-17T00:00:00Z',
  updated_at: '2026-08-17T00:00:00Z',
} as Topic

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.OPEN
  onmessage: ((event: MessageEvent) => void) | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  sent: { type: string }[] = []

  constructor() {
    FakeWebSocket.instances.push(this)
  }

  close() {}
  send(data: string) {
    const message = JSON.parse(data) as { type: string }
    this.sent.push(message)
    // 心跳没人应就会被当成假活、换掉这条连接。这里照真服务端答一声，用例才能把
    // 时钟拨过核对间隔而不掉线。
    if (message.type === 'ping') queueMicrotask(() => this.emit({ type: 'pong' } as WsServerFrame))
  }

  emit(frame: WsServerFrame) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
  }
}

const assistantBlock: Block = {
  id: 'assistant-1',
  conversation_id: topic.id,
  kind: 'message',
  author_type: 'participant',
  author: 'cheese-session',
  content: 'Still working.',
  created_at: '2026-08-17T00:00:01Z',
} as Block

// 队友的步骤清单是它发在房间里的一条消息（`todo_write`），和它别的话一样。
const checklist: Block = {
  ...assistantBlock,
  id: 'checklist-1',
  content: '✓ Read the brief\n✱ Write the fix',
  turn_id: 'one',
  meta: {
    checklist: {
      items: [
        { id: '1', subject: 'Read the brief', status: 'completed' },
        { id: '2', subject: 'Write the fix', status: 'in_progress' },
      ],
      result: null,
    },
  },
} as Block

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

const RESUME_DUE = '2026-10-30T19:20:00Z'
const RESUME_HINT = () => `约 ${clockTime(Date.parse(RESUME_DUE))} 自动重试`

/** 自动重试的一步（排上、开始）：一条运行记录，排在后端发出的第 `at` 帧。 */
function resumeFrame(event: string, id: string, at: number, meta: Record<string, unknown> = {}): WsServerFrame {
  const record: Block = {
    id,
    conversation_id: topic.id,
    kind: 'event',
    author_type: 'platform',
    author: 'cheese',
    content: '自动重试',
    turn_id: 'failing',
    meta: { event_type: event, seat: 'cheese-a', resume_id: 'resume-1', ...meta },
    created_at: '2026-10-30T19:00:05Z',
  }
  return { type: 'run_record', record, at, stream: 'backend' }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  listBlocks.mockResolvedValue({ data: [], has_more: false })
  listTopicMembers.mockResolvedValue({ data: [], total: 0 })
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

beforeAll(loadMarkdownReader)

describe('session activity', () => {
  it('reports working until every active work id finishes', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    socket.emit({ type: 'turn_started', turn_id: 'two' })
    socket.emit({ type: 'assistant_block', block: assistantBlock })
    socket.emit({ type: 'done' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    socket.emit({ type: 'turn_finished', turn_id: 'two' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([false])
  })

  // 结束的那一帧丢了，页面就一直以为还有活在跑：现场页签上那个绿点、对话里的
  // 「芝士正在处理」会一直挂着，而连接本身是好的，不会靠重连替它纠正。所以还认为
  // 有活在跑时，隔一阵用 sync 问一次服务端；它答的 room_state 说没有轮次了，就把
  // working 报回 false。
  it('a lost turn_finished still clears through the next check with the server', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    try {
      const vuetify = createVuetify({ components, directives })
      const view = render(ChatPanel, {
        props: { topic, topicList: [topic] },
        global: { plugins: [vuetify, i18n] },
      })
      await flush()

      const socket = FakeWebSocket.instances.at(-1)!
      socket.emit({ type: 'turn_started', turn_id: 'lost-end' })
      await flush()
      expect(view.emitted('working')?.at(-1)).toEqual([true])

      // 一句话都没再说：结束帧不会来了，页面自己核一次。
      socket.sent.length = 0
      vi.advanceTimersByTime(30_000)
      expect(socket.sent).toContainEqual({ type: 'sync' })

      socket.emit({ type: 'room_state', turn_ids: [], members: [], resumes: [], at: 0, stream: 'backend' })
      await flush()
      expect(view.emitted('working')?.at(-1)).toEqual([false])

      // 没有活在跑了，就不再问了。
      socket.sent.length = 0
      vi.advanceTimersByTime(60_000)
      expect(socket.sent).not.toContainEqual({ type: 'sync' })
    } finally {
      vi.useRealTimers()
    }
  })

  // 队友在房间里和别人一样：干活时对话里不另起一行「正在处理」，也没有一行跟着
  // 这一轮来去的清单。它的清单是它发的一条消息，这一轮结束了还在原处。
  it('a running turn adds no working line and no checklist row to the chat', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    socket.emit({ type: 'todo', items: [{ id: '1', subject: 'Transient step', status: 'in_progress' }] })
    await flush()
    expect(view.container.textContent).not.toMatch(/正在处理|正在交给/)
    expect(view.queryByRole('button', { name: /停止/ })).toBeNull()
    expect(view.queryByText('Transient step')).toBeNull()

    socket.emit({ type: 'assistant_block', block: checklist })
    await flush()
    expect(view.getByText('Write the fix')).toBeTruthy()

    socket.emit({
      type: 'block_updated',
      block: {
        ...checklist,
        content: '✓ Read the brief\n✓ Write the fix',
        meta: {
          edited_at: '2026-08-17T00:00:05Z',
          checklist: {
            items: [
              { id: '1', subject: 'Read the brief', status: 'completed' },
              { id: '2', subject: 'Write the fix', status: 'completed' },
            ],
            result: null,
          },
        },
      } as Block,
    })
    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.getAllByText('Write the fix')).toHaveLength(1)
    expect(view.getByText('已编辑')).toBeTruthy()
  })

  // 「现场」那一格靠这个事件在开工那一刻出现。以前它等的是第一个工具调用——而一个
  // @ 出来的 agent 可能先想上半分钟才动手，那半分钟里右边什么都没有，只有刷新
  // 一次页面才看得见它。干活的证据不是干活的开始。
  it('开工那一刻就报 working，不等第一个工具调用', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([false])
  })

  // 重连（掉线自动重连、切回这个话题）会重跑一次 loadTopic。上一轮早就结束了，
  // 而 working 是靠事件翻回去的——不报 false 的话，右边那格现场会在一个没人干活的
  // 话题上一直亮着。
  it('重新载入话题时把 working 报回 false', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    FakeWebSocket.instances.at(-1)!.emit({ type: 'turn_started', turn_id: 'one' })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([true])

    await view.rerender({ topic: { ...topic, id: 'another-topic' } as Topic, topicList: [topic] })
    await flush()
    expect(view.emitted('working')?.at(-1)).toEqual([false])
  })

  // 任务概览右边那一列「芝士这一轮在做哪一步」：它改一次清单，任务的连接就推来新的一份，
  // 概览读的那一格当场就是它，不再隔几秒去问一次。
  it('a checklist pushed on the conversation is what its overview reads', async () => {
    const vuetify = createVuetify({ components, directives })
    render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const items = [{ id: '1', subject: 'Write the fix', status: 'in_progress' as const }]
    FakeWebSocket.instances.at(-1)!.emit({ type: 'todo', items })
    await flush()

    expect(queryClient.getQueryData<TopicProgress>(keys.roomProgress(topic.id))?.items).toEqual(items)
  })

  // 一轮没完成、平台约好几点自动重试：输入框上方说这位队友在等、约几点重试，打开
  // 页面时（服务端答 sync 的 room_state）就说；重试开始了就不再说它在等。
  it('the composer shows a teammate waiting for its automatic retry until the retry starts', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic], showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({
      type: 'room_state',
      turn_ids: [],
      members: [],
      resumes: [
        {
          conversation_id: topic.id,
          seat: 'cheese-a',
          resume_id: 'resume-1',
          due_at: RESUME_DUE,
          since: '2026-10-30T19:00:00Z',
        },
      ],
      at: 1,
      stream: 'backend',
    })
    await flush()
    expect(view.getByTestId('member-activity').textContent).toContain(RESUME_HINT())

    socket.emit(resumeFrame('turn_resume_started', 'started-1', 2))
    await flush()
    expect(view.container.textContent).not.toContain(RESUME_HINT())
  })

  // 服务端答 sync 时先读库、再发现场。读库之后才排上的那一次自动重试，它的运行记录
  // 先到页面；这一轮随后结束、不再定时核对，那份读早了的现场不能把它抹掉。
  it('a room_state read before a retry was scheduled does not erase that wait', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic], showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'failing' })
    socket.emit(resumeFrame('turn_resume_wait', 'wait-1', 7, { due_at: RESUME_DUE }))
    socket.emit({ type: 'turn_finished', turn_id: 'failing' })
    socket.emit({ type: 'room_state', turn_ids: [], members: [], resumes: [], at: 6, stream: 'backend' })
    await flush()
    expect(view.getByTestId('member-activity').textContent).toContain(RESUME_HINT())
  })

  // 重连时，服务端先发 subscribed、再补发这一轮里留着的旧帧；页面在 subscribed 时就
  // 问了现场，补发的帧和那份现场谁先到都有可能。补发的那条排上记录说的队友已经移出
  // 房间，现场说不欠了：它不该再回来。
  it('a replayed wait from before the room_state does not bring back a retry the room no longer owes', async () => {
    const vuetify = createVuetify({ components, directives })
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic], showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    const socket = FakeWebSocket.instances.at(-1)!
    socket.emit({ type: 'turn_started', turn_id: 'other' })
    socket.emit({ type: 'room_state', turn_ids: ['other'], members: [], resumes: [], at: 6, stream: 'backend' })
    socket.emit(resumeFrame('turn_resume_wait', 'wait-1', 3, { due_at: RESUME_DUE }))
    socket.emit({ type: 'turn_finished', turn_id: 'other' })
    await flush()
    expect(view.container.textContent).not.toContain(RESUME_HINT())
  })
})
