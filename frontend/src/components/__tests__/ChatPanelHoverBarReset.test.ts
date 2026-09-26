/** 切话题时悬停条回到原点。
 *
 * 悬停条是绝对定位的，收起只是变透明——它仍停在上一个话题那一行的 translateY 上，
 * 仍算进这一栏的可滚动高度。从一个往上翻得很深的长话题切到只有几条的短话题，它把
 * 滚动区撑到两千多像素，滚动位置停在短话题那几行下面：整屏空白，刷新才好（dev,
 * 2026-09-26，无头 Chromium 复现：内容 189px、scrollHeight 2099px、一行都看不见）。
 * happy-dom 没有布局，所以这里钉的是原因——切过去之后条不再挂在旧的位置上。
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
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
    answerOptions: vi.fn(),
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

import { setLocale } from '@/i18n'

function room(id: string, title: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title,
    kind: 'topic',
    status: 'active',
    created_at: '2026-09-01T00:00:00Z',
  }
}

function message(roomId: string, id: string, minute: number) {
  return {
    id,
    topic_id: roomId,
    kind: 'message',
    author_type: 'participant' as const,
    author: '张衡',
    content: `第 ${minute} 条`,
    created_at: new Date(Date.UTC(2026, 8, 20, 0, minute)).toISOString(),
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function barOffset(container: Element): string {
  return (container.querySelector('.hover-bar') as HTMLElement).style.transform
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
})

describe('切话题时的悬停条', () => {
  it('不停在上一个话题那一行的位置上', async () => {
    const long = room('long-1', '长话题')
    const short = room('short-1', '短话题')
    listBlocks.mockImplementation((id: string) =>
      Promise.resolve({
        data: id === long.id ? [message(long.id, 'a1', 1), message(long.id, 'a2', 2)] : [message(short.id, 'b1', 1)],
        total: 1,
        has_more: false,
        oldest_id: null,
      })
    )
    const vuetify = createVuetify({ components, directives })
    const { container, rerender } = render(ChatPanel, {
      props: { topic: long, topicList: [long, short] },
      global: { plugins: [vuetify] },
    })
    await flush()

    // 指针停在长话题很靠下的那一行上。
    const row = container.querySelector<HTMLElement>('[data-mid="a2"]')!
    row.getBoundingClientRect = () => ({
      top: 2080,
      bottom: 2120,
      left: 0,
      right: 0,
      width: 0,
      height: 40,
      x: 0,
      y: 2080,
      toJSON() {},
    })
    await fireEvent.mouseOver(row)
    expect(barOffset(container)).toBe('translateY(2080px)')

    await rerender({ topic: short, topicList: [long, short] })
    await flush()

    expect(barOffset(container)).toBe('translateY(0px)')
  })
})
