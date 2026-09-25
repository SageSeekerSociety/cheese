// 往上翻着看旧消息的时候别人又说了话：屏幕不能被拽走，但人得知道来了新的。
import type { Component } from 'vue'
import type { Block, Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn().mockResolvedValue({ data: [], has_more: false, total: 0, oldest_id: null }),
  getProgress: vi.fn().mockResolvedValue({ items: [] }),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
  chatWsUrl: () => 'ws://test/chat',
}))

import ChatPanel from './ChatPanel.vue'

import { t } from '@/i18n'

const sockets: TestSocket[] = []
class TestSocket {
  static OPEN = 1
  readyState = 1
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  constructor() {
    sockets.push(this)
  }
}

const flush = () => vi.advanceTimersByTimeAsync(0)

function said(id: string, author = 'someone-else'): Block {
  return {
    id,
    topic_id: 't',
    kind: 'message',
    author_type: 'participant',
    author,
    content: `message ${id}`,
    created_at: new Date().toISOString(),
  } as Block
}

function arrive(block: Block) {
  sockets[0].onmessage?.({ data: JSON.stringify({ type: 'user_block', block }) })
}

// happy-dom 不排版：给滚动容器一个高度，并把它停在离底部很远的地方。
function scrollAway(pane: HTMLElement) {
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, value: 4000 })
  Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 600 })
  pane.scrollTop = 0
  pane.scrollTo = vi.fn()
  pane.dispatchEvent(new Event('scroll'))
}

async function mountRoom() {
  const view = render(ChatPanel as unknown as Component, {
    props: { topic: { id: 't', project_id: 'p', title: 'Room', kind: 'topic' } as Topic },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await flush()
  sockets[0].onopen?.()
  await vi.advanceTimersByTimeAsync(500) // 连上之后的重放追赶期过去
  return view
}

const pill = (container: Element) => container.querySelector('.new-pill')

beforeEach(() => {
  vi.useFakeTimers()
  sockets.length = 0
  vi.stubGlobal('WebSocket', TestSocket)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('往上翻着时来了新消息', () => {
  it('停在底部时不提示——新消息本来就在眼前', async () => {
    const { container } = await mountRoom()
    arrive(said('a'))
    await flush()
    expect(pill(container)).toBeNull()
  })

  it('翻上去了就提示，并数得出来了几条', async () => {
    const { container, getByTestId } = await mountRoom()
    scrollAway(getByTestId('chat-scroll'))
    arrive(said('a'))
    arrive(said('b'))
    await flush()
    expect(pill(container)?.textContent).toContain('2')
    expect(pill(container)?.textContent).toContain(t('work.room.newMessages'))
  })

  it('自己说的话不算新消息', async () => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
    const { container, getByTestId } = await mountRoom()
    scrollAway(getByTestId('chat-scroll'))
    arrive(said('mine', 'me'))
    await flush()
    expect(pill(container)).toBeNull()
    localStorage.removeItem('user')
  })

  it('点一下回到最新，提示收起', async () => {
    const { container, getByTestId } = await mountRoom()
    const pane = getByTestId('chat-scroll')
    scrollAway(pane)
    arrive(said('a'))
    await flush()
    await fireEvent.click(pill(container)!)
    await vi.advanceTimersByTimeAsync(400)
    expect(pane.scrollTo).toHaveBeenCalled()
    expect(pill(container)).toBeNull()
  })
})
