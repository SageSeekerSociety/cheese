import type { Topic, WsServerFrame } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getAgentControl, sendAgentControl } from '@/api'
import { setLocale } from '@/i18n'

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAgentControl: vi.fn(),
    sendAgentControl: vi.fn(),
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false }),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/chat',
  }
})

import ChatPanel from './ChatPanel.vue'

const topic = {
  id: 'stop-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Stop',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
} as Topic

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.OPEN
  onmessage: ((event: MessageEvent) => void) | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor() {
    FakeWebSocket.instances.push(this)
  }

  close() {}
  send() {}

  emit(frame: WsServerFrame) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent)
  }
}

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

async function working() {
  const view = render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await flush()
  const socket = FakeWebSocket.instances.at(-1)!
  socket.emit({ type: 'turn_started', turn_id: 'one' })
  await flush()
  return { view, socket }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.mocked(getAgentControl).mockResolvedValue({ id: 's1', connected: true })
})

describe('stopping a run from the conversation', () => {
  it('offers stop beside the working line and sends the interrupt to the live session', async () => {
    vi.mocked(sendAgentControl).mockResolvedValue({
      request_id: 'r1',
      status: 'completed',
      result: { response: { subtype: 'success', response: { stopped: true } } },
    })
    const { view, socket } = await working()
    expect(view.queryByText('停止')).toBeTruthy()

    await fireEvent.click(view.getByText('停止'))
    await flush()
    expect(sendAgentControl).toHaveBeenCalledWith('stop-topic', 's1', { subtype: 'interrupt' })

    socket.emit({ type: 'turn_finished', turn_id: 'one' })
    await flush()
    expect(view.queryByText('停止')).toBeNull()
  })

  it('says why when there is nothing it could stop', async () => {
    vi.mocked(getAgentControl).mockResolvedValue({ id: null, connected: false })
    const { view } = await working()

    await fireEvent.click(view.getByText('停止'))
    await flush()
    expect(sendAgentControl).not.toHaveBeenCalled()
    expect(view.getByText('停止失败：芝士尚未开始运行')).toBeTruthy()
  })

  it('shows the error the stop came back with', async () => {
    vi.mocked(sendAgentControl).mockRejectedValue(new Error('No session is running in this room'))
    const { view } = await working()

    await fireEvent.click(view.getByText('停止'))
    await flush()
    expect(view.getByText('停止失败：No session is running in this room')).toBeTruthy()
  })

  it('is not there when nothing is running', async () => {
    const view = render(ChatPanel, {
      props: { topic, topicList: [topic] },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await flush()
    expect(view.queryByText('停止')).toBeNull()
  })
})
