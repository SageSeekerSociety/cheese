/** 改自己发过的消息。
 *
 * 悬停条上的「编辑」只出现在自己说的话上；点下去正文原地换成输入框，保存后房间里
 * 显示新的正文并标「已编辑」。别人的编辑经 `block_updated` 帧到达，同样原地换掉。
 */
import type { Block, Topic, WsServerFrame } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const editMessage = vi.fn()

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
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
    attachmentRawUrl: () => '',
    editMessage: (...a: unknown[]) => editMessage(...a),
  }
})

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

function said(id: string, author: string, content: string): Block {
  return {
    id,
    conversation_id: topic.id,
    kind: 'message',
    author_type: 'participant',
    author,
    content,
    created_at: '2026-09-20T00:01:00Z',
  } as Block
}

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
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  listBlocks.mockResolvedValue({
    data: [said('mine', 'alice', '周五交初稿'), said('theirs', 'bob', '好的')],
    has_more: false,
    oldest_id: null,
  })
})

async function mount() {
  const vuetify = createVuetify({ components, directives })
  const view = render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [vuetify, i18n] },
  })
  await flush()
  return view
}

async function hover(container: Element, id: string) {
  await fireEvent.mouseOver(container.querySelector<HTMLElement>(`[data-mid="${id}"]`)!)
}

describe('改自己发过的消息', () => {
  it('只有自己的消息上有「编辑」', async () => {
    const view = await mount()
    await hover(view.container, 'theirs')
    expect(view.queryByTitle('编辑')).toBeNull()
    await hover(view.container, 'mine')
    expect(view.getByTitle('编辑')).toBeTruthy()
  })

  it('原地改、保存之后显示新的正文和「已编辑」', async () => {
    editMessage.mockImplementation(async (id: string, content: string) => ({
      ...said(id, 'alice', content),
      meta: { edited_at: '2026-09-20T00:02:00Z' },
    }))
    const view = await mount()
    await hover(view.container, 'mine')
    await fireEvent.click(view.getByTitle('编辑'))
    await flush()

    const box = view.getByRole('textbox', { name: '编辑' }) as HTMLTextAreaElement
    expect(box.value).toBe('周五交初稿')
    await fireEvent.update(box, '周六交初稿')
    await fireEvent.click(view.getByRole('button', { name: '保存' }))
    await flush()

    expect(editMessage).toHaveBeenCalledWith('mine', '周六交初稿')
    expect(view.queryByRole('textbox', { name: '编辑' })).toBeNull()
    expect(view.getByText('周六交初稿')).toBeTruthy()
    expect(view.getByText('已编辑')).toBeTruthy()
  })

  it('Esc 放弃，原文不动', async () => {
    const view = await mount()
    await hover(view.container, 'mine')
    await fireEvent.click(view.getByTitle('编辑'))
    await flush()
    const box = view.getByRole('textbox', { name: '编辑' })
    await fireEvent.update(box, '改了一半')
    await fireEvent.keyDown(box, { key: 'Escape' })
    await flush()

    expect(editMessage).not.toHaveBeenCalled()
    expect(view.queryByRole('textbox', { name: '编辑' })).toBeNull()
    expect(view.getByText('周五交初稿')).toBeTruthy()
    expect(view.queryByText('已编辑')).toBeNull()
  })

  it('别人改了消息，房间里原地换成新的正文', async () => {
    const view = await mount()
    FakeWebSocket.instances.at(-1)!.emit({
      type: 'block_updated',
      block: { ...said('theirs', 'bob', '好的，周六见'), meta: { edited_at: '2026-09-20T00:02:00Z' } },
    })
    await flush()
    expect(view.queryByText('好的')).toBeNull()
    expect(view.getByText('好的，周六见')).toBeTruthy()
    expect(view.getByText('已编辑')).toBeTruthy()
  })
})
