/** 打开一条支线就算读过它；页头一行写它说的事，「转为任务」把这条支线原地转成任务，转成以后
 *  页头写任务名、状态、负责人和「整页打开」。从这里另开的几件任务列在挂着的那条消息下面。 */
import type { Component } from 'vue'
import type { Block, RoomTask, Topic } from '@/cx_types'
import type { ThreadSummary } from '@/types/threads'

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
  getTask: vi.fn(async (id: string) => roomTasks.value.find((task) => task.id === id)),
}))

// 对话那一栏也会问这段对话里有没有待存的技能提议：这里没有。
vi.mock('./useSkillProposals', async () => {
  const { ref } = await import('vue')
  return {
    useSkillProposals: () => ({
      proposals: ref([]),
      saved: ref([]),
      busy: ref(null),
      error: ref(null),
      load: async () => {},
      save: async () => {},
      decline: async () => {},
    }),
  }
})

import ThreadPane from './ThreadPane.vue'

import { setLocale } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

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

function attachment(id: string, path: string, mime = 'application/pdf'): Block {
  return {
    id,
    conversation_id: 'r1',
    kind: 'attachment',
    author_type: 'participant',
    author: 'alice',
    content: path,
    mime_type: mime,
    created_at: '2026-10-06T00:00:01Z',
  } as Block
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

/** 主线上一块带着自己那条支线的消息（`GET /topics/{频道}/blocks` 就是这样的）。 */
function summary(): ThreadSummary {
  return {
    id: 'th1',
    room_id: 'r1',
    root_block_id: 'b1',
    reply_count: 2,
    last_reply_at: '2026-10-06T00:01:00Z',
    last_reply: { author: 'alice', content: '好', created_at: '2026-10-06T00:01:00Z' },
    participants: ['alice'],
    tasks: [],
  }
}

// 对话那一栏的替身：支线页头那条消息要从它自己的滚动区里进去（真身上是 ChatTimeline 的
// #timeline-start，落在 `.messages` 那一格里），所以替身也把两个槽摆出来。槽摆出来，
// 页脚那张反馈卡就跟着挂了上来（真卡片要 pinia 和两个仓库），照下面那条用例的做法换成替身。
const CHAT_PANEL_STUB = {
  template:
    '<div data-testid="chat-scroll"><slot name="timeline-start" /><slot name="timeline-end" /><slot name="above-composer" /></div>',
}

function mount() {
  return render(ThreadPane as Component, {
    props: { room: ROOM, threadId: 'th1', members: [], topicList: [], memberNames: { alice: '李安' } },
    global: {
      plugins: [vuetify],
      stubs: {
        ChatPanel: CHAT_PANEL_STUB,
        MarkdownView: true,
        // 图片本身从哪取字节是另一件事；这里只看它拿到的路径对不对。
        AttachmentImage: {
          props: ['topicId', 'path'],
          template: '<div data-testid="attachment-image" :data-path="path" />',
        },
        AgentFeedbackCard: true,
        TopicAcceptCard: true,
      },
    },
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

  it('原消息一起发出去的图片和文件，也画在支线这条消息下面', async () => {
    getThread.mockResolvedValue({
      id: 'th1',
      room_id: 'r1',
      root_block_id: 'b1',
      reply_count: 2,
      root: root(),
      files: [attachment('a1', 'uploads/shot.png', 'image/png'), attachment('a2', 'uploads/spec.pdf')],
    })
    const { findByTestId, findByText } = mount()
    const image = await findByTestId('attachment-image')
    expect(image.getAttribute('data-path')).toBe('uploads/shot.png')
    expect(await findByText('spec.pdf')).toBeTruthy()
  })

  it('只发了图片、没有字的那条消息，支线里画的也是那张图', async () => {
    getThread.mockResolvedValue({
      id: 'th1',
      room_id: 'r1',
      root_block_id: 'b1',
      reply_count: 2,
      root: attachment('b1', 'uploads/shot.png', 'image/png'),
    })
    const { findByTestId } = mount()
    const image = await findByTestId('attachment-image')
    expect(image.getAttribute('data-path')).toBe('uploads/shot.png')
  })

  it('「转为任务」转的是它挂着的那条消息：这条支线原地转', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    roomTasks.value = [task('task9', 'b1')]
    const { findByTestId, emitted } = mount()
    await fireEvent.click(await findByTestId('thread-to-task'))
    expect(emitted()['to-task']).toEqual([['b1']])
  })

  it('页头只有一行，写那条消息说的事，开头点的名不写', async () => {
    getThread.mockResolvedValue({
      id: 'th1',
      room_id: 'r1',
      root_block_id: 'b1',
      reply_count: 2,
      root: root({ content: '<@cheese-1> 登录要不要加手机验证码？' }),
    })
    const { findByRole } = mount()
    const heading = await findByRole('heading', { level: 2 })
    await waitFor(() => expect(heading.textContent).toBe('登录要不要加手机验证码？'))
  })

  it('转成任务以后，页头写任务名、状态和负责人，整页打开去任务页', async () => {
    roomTasks.value = [{ ...task('th1', 'b1', '登录验证码'), presentation: { column: 'building', phrase: 'running' } }]
    getThread.mockResolvedValue({
      id: 'th1',
      room_id: 'r1',
      root_block_id: 'b1',
      reply_count: 2,
      root: root(),
      task: 'th1',
    })
    const { findByRole, findByTestId, queryByTestId, getByText, emitted } = mount()
    const heading = await findByRole('heading', { level: 2 })
    await waitFor(() => expect(heading.textContent).toBe('登录验证码'))
    expect(getByText('李安 负责')).toBeTruthy()
    expect(queryByTestId('thread-to-task')).toBeNull()
    // 这条支线自己转成的那件写在页头，不在下面再列一遍。
    expect(queryByTestId('task-card')).toBeNull()
    await fireEvent.click(await findByTestId('thread-open-page'))
    expect(emitted()['open-page']).toEqual([['th1']])
  })

  // 任务都在右半边打开：等人审阅的那一条（采纳框）在这里也看得见，不用先去整页。
  it('打开的是任务时，输入框上方有这件任务的审阅那一条；支线没有', async () => {
    roomTasks.value = [task('th1', 'b1', '登录验证码')]
    getThread.mockResolvedValue({
      id: 'th1',
      room_id: 'r1',
      root_block_id: 'b1',
      reply_count: 2,
      root: root(),
      task: 'th1',
    })
    const asTask = mount()
    await waitFor(() => expect(asTask.container.querySelector('topic-accept-card-stub')).not.toBeNull())
    expect(asTask.container.querySelector('topic-accept-card-stub')?.getAttribute('taskid')).toBe('th1')
    asTask.unmount()

    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    queryClient.clear()
    const asThread = mount()
    await asThread.findByTestId('thread-to-task')
    expect(asThread.container.querySelector('topic-accept-card-stub')).toBeNull()
  })

  it('从这里另开的任务都列着，点开去那件任务', async () => {
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

  it('页头这条初始消息高过一段阅读高度就收起，点开能展开', async () => {
    // 判据是渲染后的高度（lib/chatFold），不是字数。happy-dom 没有布局，量不到真实
    // 的 scrollHeight，这里把它替成 600px（高过 20 行 × 24px 行高的预算），让正文层
    // 长得像一条长消息。行高由替身自己的 inline style 给出——happy-dom 的
    // getComputedStyle 读不出样式表里的行高，只认 inline 的。
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('thread-root__text') ? 600 : 0
      },
    })
    try {
      getThread.mockResolvedValue({
        id: 'th1',
        room_id: 'r1',
        root_block_id: 'b1',
        reply_count: 2,
        // 队友署名（cheese-<hex>）走 MarkdownView 那一支；替身自己带 inline 行高，
        // happy-dom 的 getComputedStyle 读不出样式表里的行高。
        root: root({ author_type: 'participant', author: 'cheese-1', content: '很长的一段回复' }),
      })
      const { container, findByTestId } = render(ThreadPane as Component, {
        props: { room: ROOM, threadId: 'th1', members: [], topicList: [], memberNames: { alice: '李安' } },
        global: {
          plugins: [vuetify],
          stubs: {
            ChatPanel: CHAT_PANEL_STUB,
            AgentFeedbackCard: true,
            MarkdownView: {
              props: ['source'],
              template: '<div class="thread-root__text" style="line-height: 24px">{{ source }}</div>',
            },
          },
        },
      })
      // 量高排在下一帧的批次里（lib/foldMeasure）。
      const toggle = await findByTestId('thread-root-fold')
      await waitFor(() => expect(container.querySelector('.fold-clamped')).not.toBeNull())
      expect(toggle.textContent).toContain('展开')

      await fireEvent.click(toggle)
      await waitFor(() => expect(container.querySelector('.fold-clamped')).toBeNull())
      expect((await findByTestId('thread-root-fold')).textContent).toContain('收起')
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', original)
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight
    }
  })

  it('页头这条初始消息在对话的滚动区里，跟回复一起滚', async () => {
    // 不钉在面板顶上：钉着的话，它一展开就把下面的回复挤没、把正文末尾那颗「收起」顶到
    // 看不见的地方——点开就再收不回来。对话的滚动区是 ChatPanel 里那一格，页头这条消息
    // 从它的 #timeline-start 进去（替身摆的是同一个位置关系）。
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('[data-testid="chat-scroll"] .thread-root')).not.toBeNull())
  })

  it('手上已经有它挂着的那条消息时，页头不等支线读回来就在', async () => {
    // 打开一条支线时页头那条「开局消息」不该空着等 `GET /topics/{支线}/thread`：点它的
    // 那条消息本来就在频道的对话窗口里。这里让那次读永远不回来，页头照样要在。
    queryClient.setQueryData(keys.roomNewestBlocks('r1'), {
      blocks: [{ ...root(), thread: summary() }],
      hasMore: false,
    })
    getThread.mockReturnValue(new Promise(() => {}))
    const { findByText } = mount()
    expect(await findByText('李安')).toBeTruthy()
  })

  it('窗口里没有那条消息、支线列表里那一行带着完整的开局消息时，页头也不等', async () => {
    // 从频道概览的支线列表点开一条老支线：它挂着的那条消息早已翻出最新那一段。列表那一行
    // 带着那条消息（没截过），先拿它画上。
    queryClient.clear()
    queryClient.setQueryData(keys.roomThreads('r1'), [
      {
        ...summary(),
        root: {
          author: 'alice',
          author_type: 'participant',
          kind: 'message',
          content: '首页要改哪些地方？',
          truncated: false,
          created_at: '2026-10-06T00:00:00Z',
        },
        unread: false,
      },
    ])
    getThread.mockReturnValue(new Promise(() => {}))
    const { findByText } = mount()
    expect(await findByText('李安')).toBeTruthy()
  })

  it('列表那一行里的开局消息被截过，就不拿它当整条消息画', async () => {
    queryClient.clear()
    queryClient.setQueryData(keys.roomThreads('r1'), [
      {
        ...summary(),
        root: {
          author: 'alice',
          author_type: 'participant',
          kind: 'message',
          content: '长'.repeat(200),
          truncated: true,
          created_at: '2026-10-06T00:00:00Z',
        },
        unread: false,
      },
    ])
    getThread.mockReturnValue(new Promise(() => {}))
    const { container } = mount()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(container.querySelector('.thread-root')).toBeNull()
  })

  it('反馈卡接在支线的对话后面', async () => {
    getThread.mockResolvedValue({ id: 'th1', room_id: 'r1', root_block_id: 'b1', reply_count: 2, root: root() })
    const { container } = render(ThreadPane as Component, {
      props: { room: ROOM, threadId: 'th1', members: [], topicList: [], memberNames: { alice: '李安' } },
      global: {
        plugins: [vuetify],
        stubs: {
          MarkdownView: true,
          ChatPanel: CHAT_PANEL_STUB,
          AgentFeedbackCard: { props: ['topicId'], template: '<i class="feedback-probe">{{ topicId }}</i>' },
        },
      },
    })
    await waitFor(() => expect(container.querySelector('.feedback-probe')?.textContent).toBe('th1'))
  })

  it('输入框说给这条支线正在和它说话的那位队友，不是频道默认那位', async () => {
    getThread.mockResolvedValue({
      id: 'th-mate',
      room_id: 'r1',
      root_block_id: 'b1',
      reply_count: 2,
      root: root(),
      teammate: 'cheese-opus',
    })
    const { container } = render(ThreadPane as Component, {
      props: { room: ROOM, threadId: 'th-mate', members: [], topicList: [], memberNames: { alice: '李安' } },
      global: {
        plugins: [vuetify],
        stubs: {
          MarkdownView: true,
          AgentFeedbackCard: true,
          ChatPanel: { props: ['agentHandle'], template: '<i class="composer-probe">{{ agentHandle }}</i>' },
        },
      },
    })
    await waitFor(() => expect(container.querySelector('.composer-probe')?.textContent).toBe('cheese-opus'))
  })
})
