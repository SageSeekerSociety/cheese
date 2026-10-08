/** 频道主线上的任务卡。
 *
 * 一件任务在主线上只有一张卡：从一条消息（或它的支线）出来的，挂在那条消息下面；单独
 * 新建的，是新建它的人在那一刻发出的一条。卡在原处变化，主线底部不再追加任何一行。
 *
 * 2026-08-11 09:04 一件活被拆到子话题，父话题这边**什么都没变**，于是房间照着自己那
 * 份清单又做了一遍同一件事（issue #314）：主线上看得见「这件事已经有任务了」是这里要
 * 钉住的。挂真实的 ChatPanel、喂真实的消息和任务列表、从 DOM 上读结果。
 */
import type { RoomTask, Topic } from '../../cx_types'
import type { ChannelTask } from '../../lib/channelTasks'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const listRoomTasks = vi.fn()

vi.mock('../../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    // 一件活不再是话题列表里的一行，标记要从房间的支线里读。
    listRoomTasks: (...a: unknown[]) => listRoomTasks(...a),
    // 面板打开时顺手要的东西 —— 安静地给空答案。
    attachmentRawUrl: () => '',
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

let seq = 0
/** 每个用例一个新房间 id —— 时间线窗口有个模块级缓存，共用 id 会串味。 */
function freshRoom(): string {
  seq += 1
  return `room-${seq}`
}

function room(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: 'issue 187',
    kind: 'topic',
    status: 'active',
    created_at: '2026-08-10T00:00:00Z',
  }
}

function message(roomId: string, id: string, createdAt: string, content: string) {
  return {
    id,
    conversation_id: roomId,
    kind: 'message',
    author_type: 'participant' as const,
    author: '张衡',
    content,
    created_at: createdAt,
  }
}

function work(
  roomId: string,
  id: string,
  title: string,
  createdAt: string,
  extra: Partial<ChannelTask> = {}
): ChannelTask {
  return {
    id,
    project_id: 'p1',
    room_id: roomId,
    title,
    status: 'open',
    created_at: createdAt,
    updated_at: createdAt,
    // 落哪一列、写哪句话，全由后端给。这一份用例不关心是哪一列，但字段必须在。
    presentation: { column: 'building', phrase: 'running' },
    ...extra,
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mountPanel(topic: Topic, tasks: RoomTask[]) {
  listRoomTasks.mockResolvedValue({ data: tasks, total: tasks.length })
  const vuetify = createVuetify({ components, directives })
  return render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [vuetify, i18n] },
  })
}

/** 时间线上从上到下的行：消息记 block id，任务卡记 t:<任务id>。 */
function timelineOrder(container: Element): string[] {
  const rows = container.querySelectorAll('[data-mid],[data-testid="task-card"]')
  return Array.from(rows).map((el) => el.getAttribute('data-mid') ?? `t:${el.getAttribute('data-task-id')}`)
}

function card(container: Element, taskId: string): HTMLElement {
  return container.querySelector<HTMLElement>(`[data-testid="task-card"][data-task-id="${taskId}"]`)!
}

function status(container: Element, taskId: string): HTMLElement {
  return card(container, taskId).querySelector<HTMLElement>('[data-testid="task-card-status"]')!
}

beforeAll(() => {
  // Vuetify 的 layout/overlay 摸这个 API，happy-dom 没有。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 面板打开会连 WebSocket；这里只看渲染，给个不做事的替身。
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  // 标记上的字走文案目录，而 happy-dom 起步是英文。
  setLocale('zh-CN')
  vi.clearAllMocks()
})

describe('频道主线上的任务卡', () => {
  it('单独新建的任务，卡落在新建的那一刻，夹在前后两条消息之间', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [
        message(id, 'b1', '2026-08-11T09:00:00Z', '这轮要做三项'),
        message(id, 'b2', '2026-08-11T09:30:00Z', '第 2 项我来'),
      ],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z')])
    await flush()

    expect(timelineOrder(container)).toEqual(['b1', 't:sub-1', 'b2'])
    expect(card(container, 'sub-1').textContent).toContain('进度层与记忆落地')
  })

  // 一件任务不是地点：它没有 /topics/<id> 那一页，点它是在这个频道里打开那件任务。
  it('点卡 = 在这个频道里打开那件任务', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const { container, emitted } = mountPanel(room(id), [work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z')])
    await flush()

    await fireEvent.click(card(container, 'sub-1'))

    expect(emitted()['open-card']).toEqual([['sub-1']])
    expect(emitted()['open-topic']).toBeUndefined()
  })

  it('刚新建、之后频道里还没人说话 —— 卡排在最后一条消息下面', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z')])
    await flush()

    expect(timelineOrder(container)).toEqual(['b1', 't:sub-1'])
  })

  it('任务做完了，卡在原处改口', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const done = work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z', {
      status: 'closed',
      presentation: { column: 'done', phrase: 'completed' },
    })
    const { container } = mountPanel(room(id), [done])
    await flush()

    expect(status(container, 'sub-1').dataset.tone).toBe('done')
    expect(timelineOrder(container)).toEqual(['b1', 't:sub-1'])
  })

  it('任务在等人审阅时，卡上说在等谁', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const waiting = work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z', {
      presentation: { column: 'needs_you', phrase: 'awaiting_review' },
      waiting_on: 'linxiao',
    })
    const { container } = mountPanel(room(id), [waiting])
    await flush()

    expect(status(container, 'sub-1').dataset.tone).toBe('waiting')
    expect(status(container, 'sub-1').textContent).toContain('linxiao')
  })

  it('频道里没有任务时，时间线一如既往', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [])
    await flush()

    expect(container.querySelectorAll('[data-testid="task-card"]')).toHaveLength(0)
    expect(timelineOrder(container)).toEqual(['b1'])
  })

  // 一条消息可以出好几件任务：都挂在那条消息下面，主线上不在别处再记一笔。
  it('从一条消息出来的任务都挂在它下面，不在主线别处再出现', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [
        message(id, 'b1', '2026-08-11T09:00:00Z', '报名表单太长了'),
        message(id, 'b2', '2026-08-11T10:00:00Z', '好'),
      ],
      has_more: false,
    })

    const { container, emitted } = mountPanel(room(id), [
      work(id, 'sub-1', '表单字段精简', '2026-08-11T09:04:31Z', { upgraded_from_block_id: 'b1' }),
      work(id, 'sub-2', '学号格式校验', '2026-08-11T09:20:00Z', { upgraded_from_block_id: 'b1' }),
    ])
    await flush()

    expect(timelineOrder(container)).toEqual(['b1', 't:sub-1', 't:sub-2', 'b2'])
    expect(container.querySelectorAll('[data-testid="task-created-post"]')).toHaveLength(0)
    await fireEvent.click(card(container, 'sub-2'))
    expect(emitted()['open-card']).toEqual([['sub-2']])
  })

  it('正文里 <#活的id> 写出活的标题，点下去打开那张卡；<#话题id> 仍然打开话题', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:10:00Z', '见 <#sub-1>，另见 <#' + id + '>')],
      has_more: false,
    })

    const { container, emitted } = mountPanel(room(id), [work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z')])
    await flush()

    const chips = Array.from(container.querySelectorAll<HTMLElement>('.topic-ref'))
    const taskChip = chips.find((c) => c.dataset.topic === 'sub-1')!
    expect(taskChip.textContent).toBe('#进度层与记忆落地')
    await fireEvent.click(taskChip)
    await fireEvent.click(chips.find((c) => c.dataset.topic === id)!)

    expect(emitted()['open-card']).toEqual([['sub-1']])
    expect(emitted()['open-topic']).toEqual([[id]])
  })

  it('「派出一条活」那一行带一颗按钮，打开它派出去的那张卡', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [
        {
          ...message(id, 'e1', '2026-08-11T09:04:32Z', '派出一条活：进度层与记忆落地'),
          kind: 'event',
          author: 'system',
          meta: { platform: true, action: 'split', task_id: 'sub-1' },
        },
      ],
      has_more: false,
    })

    const { getByText, emitted } = mountPanel(room(id), [])
    await flush()

    await fireEvent.click(getByText('查看任务'))

    expect(emitted()['open-card']).toEqual([['sub-1']])
  })

  // 「窗口上面还有没加载的历史时，落在窗口之前的标记先不显示」这条只在
  // splitMarkers.spec.ts 里测：那种状态下面板会自己往回翻页把视口填满，而 happy-dom
  // 里所有元素高度都是 0、永远填不满，于是翻页停不下来 —— 挂真实组件测不了它。
})
