/** 时间线上的「已派出」标记 (issue #314)。
 *
 * 2026-08-11 09:04 一件活被拆到子话题，父话题这边**什么都没变**：`cheese split`
 * 不往父话题写任何 block。于是房间照着自己那份清单又做了一遍同一件事，两套不兼容
 * 的迁移各自跑绿，全靠人工比对文件列表才发现。
 *
 * 这里挂真实的 ChatPanel、喂真实的消息和话题列表、从 DOM 上读结果 —— 要钉的就是
 * 「人打开父话题，眼睛能不能看见这件事已经不归这里了」，而不是某个函数返回了什么。
 */
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    // 面板打开时顺手要的东西 —— 安静地给空答案。
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
    answerOptions: vi.fn(),
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

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
    topic_id: roomId,
    kind: 'message',
    author_type: 'human' as const,
    author: '张衡',
    content,
    created_at: createdAt,
  }
}

function work(roomId: string, id: string, title: string, createdAt: string, extra: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: roomId,
    title,
    kind: 'task',
    status: 'active',
    created_at: createdAt,
    ...extra,
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mountPanel(topic: Topic, topicList: Topic[]) {
  const vuetify = createVuetify({ components, directives })
  return render(ChatPanel, {
    props: { topic, topicList },
    global: { plugins: [vuetify] },
  })
}

/** 时间线上从上到下的行：消息记 block id，派出标记记 t:<子话题id>。 */
function timelineOrder(container: Element): string[] {
  const rows = container.querySelectorAll('[data-mid],[data-testid="dispatched-marker"]')
  return Array.from(rows).map((el) => el.getAttribute('data-mid') ?? `t:${el.getAttribute('data-topic-id')}`)
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
  vi.clearAllMocks()
})

describe('父话题时间线上的「已派出」标记', () => {
  it('标在活被拆出去的那一刻，夹在前后两条消息之间', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [
        message(id, 'b1', '2026-08-11T09:00:00Z', '这轮要做三项'),
        message(id, 'b2', '2026-08-11T09:30:00Z', '第 2 项我来'),
      ],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [
      room(id),
      work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z'),
    ])
    await flush()

    expect(timelineOrder(container)).toEqual(['b1', 't:sub-1', 'b2'])
    const marker = container.querySelector('[data-testid="dispatched-marker"]')!
    expect(marker.textContent).toContain('进度层与记忆落地')
    expect(marker.textContent).toContain('这部分在该话题进行')
  })

  it('点标记上的标题 = 打开那个子话题', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const { container, emitted } = mountPanel(room(id), [
      room(id),
      work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z'),
    ])
    await flush()

    const link = container.querySelector('[data-testid="dispatched-marker"] button')!
    await fireEvent.click(link)

    expect(emitted()['open-topic']).toEqual([['sub-1']])
  })

  it('刚拆出去、之后房间里还没人说话 —— 标记排在最后一条消息下面', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [
      room(id),
      work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z'),
    ])
    await flush()

    expect(timelineOrder(container)).toEqual(['b1', 't:sub-1'])
  })

  it('派出去的活做完了，标记跟着改口', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [
      room(id),
      work(id, 'sub-1', '进度层与记忆落地', '2026-08-11T09:04:31Z', { status: 'archived' }),
    ])
    await flush()

    expect(container.querySelector('[data-testid="dispatched-marker"]')!.textContent).toContain('这部分已在该话题完成')
  })

  it('房间里没派出去任何活时，时间线一如既往', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [message(id, 'b1', '2026-08-11T09:00:00Z', '开工')],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [room(id)])
    await flush()

    expect(container.querySelectorAll('[data-testid="dispatched-marker"]')).toHaveLength(0)
    expect(timelineOrder(container)).toEqual(['b1'])
  })

  // 「讨论升级」那条路径本来就在源消息上渲染了「已升级为话题，点击查看」。再标一行
  // 派出，就是同一件事在同一屏说两遍。
  it('从某条消息升级出去的话题不重复标', async () => {
    const id = freshRoom()
    listBlocks.mockResolvedValue({
      data: [
        {
          ...message(id, 'b1', '2026-08-11T09:00:00Z', '这条我们单开一个话题'),
          upgraded_to_topic_id: 'sub-1',
        },
        message(id, 'b2', '2026-08-11T10:00:00Z', '好'),
      ],
      has_more: false,
    })

    const { container } = mountPanel(room(id), [
      room(id),
      work(id, 'sub-1', '单开的话题', '2026-08-11T09:04:31Z', { upgraded_from_block_id: 'b1' }),
    ])
    await flush()

    expect(container.querySelectorAll('[data-testid="dispatched-marker"]')).toHaveLength(0)
    expect(container.textContent).toContain('已升级为话题')
  })

  // 「窗口上面还有没加载的历史时，落在窗口之前的标记先不显示」这条只在
  // splitMarkers.spec.ts 里测：那种状态下面板会自己往回翻页把视口填满，而 happy-dom
  // 里所有元素高度都是 0、永远填不满，于是翻页停不下来 —— 挂真实组件测不了它。
})
