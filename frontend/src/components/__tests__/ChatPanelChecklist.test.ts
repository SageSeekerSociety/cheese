/** 自己的清单。
 *
 * 写清单的人点一步前面的记号，那一步换到下一个状态，存进去的是改过之后的整份清单，
 * 指名改的就是这一张；别人的清单只能看。存不进去时那一步回到原来的样子。
 */
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const writeChecklist = vi.fn()

vi.mock('../../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('../../me', () => ({ myHandle: () => 'alice', myId: () => '' }))

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
  }
})

vi.mock('../../api/checklist', () => ({
  writeChecklist: (...a: unknown[]) => writeChecklist(...a),
}))

import ChatPanel from '../ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const topic: Topic = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '房间',
  kind: 'topic',
  status: 'active',
  created_at: '2026-09-01T00:00:00Z',
} as Topic

function checklist(id: string, author: string, subjects: string[]): Block {
  return {
    id,
    conversation_id: topic.id,
    kind: 'message',
    author_type: 'participant',
    author,
    content: subjects.map((s) => `○ ${s}`).join('\n'),
    created_at: '2026-09-20T00:01:00Z',
    meta: {
      checklist: {
        items: subjects.map((subject, i) => ({ id: String(i + 1), subject, status: 'pending' })),
        result: null,
      },
    },
  } as Block
}

class FakeWebSocket {
  static OPEN = 1
  readyState = FakeWebSocket.OPEN
  onmessage: ((event: MessageEvent) => void) | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  close() {}
  send() {}
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  vi.stubGlobal('WebSocket', FakeWebSocket)
  listBlocks.mockResolvedValue({
    data: [checklist('mine', 'alice', ['订会议室', '发通知']), checklist('theirs', 'bob', ['写周报'])],
    has_more: false,
    oldest_id: null,
  })
})

async function mount() {
  const view = render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await flush()
  return view
}

function row(container: Element, id: string): HTMLElement {
  return container.querySelector<HTMLElement>(`[data-mid="${id}"]`)!
}

describe('自己的清单', () => {
  it('点一步的记号，存进去的是改过之后的整份，指名这一张', async () => {
    writeChecklist.mockResolvedValue({ items: [], message_id: 'mine', posted: false })
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: /发通知/ }))
    expect(writeChecklist).toHaveBeenCalledWith(
      'room-1',
      [
        { content: '订会议室', status: 'pending' },
        { content: '发通知', status: 'in_progress' },
      ],
      { message: 'mine' }
    )
  })

  it('别人的清单上没有可点的记号', async () => {
    const view = await mount()
    expect(row(view.container, 'theirs').querySelector('.checklist__toggle')).toBeNull()
    expect(row(view.container, 'mine').querySelectorAll('.checklist__toggle')).toHaveLength(2)
    expect(view.queryByRole('button', { name: /写周报/ })).toBeNull()
  })

  it('存不进去时，那一步回到原来的状态', async () => {
    writeChecklist.mockRejectedValue(new Error('保存失败'))
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: /发通知/ }))
    await flush()
    const step = view.getByText('发通知').closest('li')!
    expect(step.className).toContain('is-pending')
  })
})
