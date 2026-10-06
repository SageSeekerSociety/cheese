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
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
  chatWsUrl: () => 'ws://test/chat',
}))

// 进房间时那次「我还欠哪些组」的读：这一条不关心组题，答「没有」即可，免得它落到
// setup-network 的「每个请求都要 mock」守卫上。
vi.mock('../services/askGroups', async () => ({
  ...(await vi.importActual<typeof import('../services/askGroups')>('../services/askGroups')),
  listAwaitingAskGroups: vi.fn().mockResolvedValue([]),
}))

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale, t } from '@/i18n'

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
    conversation_id: 't',
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

// happy-dom 不排版：给滚动容器一个高度，并让人从底部往上翻到离底部很远的地方。
function scrollAway(pane: HTMLElement) {
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, value: 4000 })
  Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 600 })
  pane.scrollTo = vi.fn()
  pane.scrollTop = 3400
  pane.dispatchEvent(new Event('scroll'))
  pane.scrollTop = 0
  pane.dispatchEvent(new Event('scroll'))
}

async function mountRoom() {
  const view = render(ChatPanel as unknown as Component, {
    props: { topic: { id: 't', project_id: 'p', title: 'Room', kind: 'topic' } as Topic },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await flush()
  sockets[0].onopen?.()
  await vi.advanceTimersByTimeAsync(500) // 连上之后的重放追赶期过去
  return view
}

const pill = (container: Element) => container.querySelector('.new-pill')

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
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
    // 翻上去了，入口还在（写「回到最新」）；但自己说的那条没有变成「新消息」去计数。
    expect(pill(container)?.textContent).toContain(t('work.room.backToLatest'))
    expect(pill(container)?.textContent).not.toContain(t('work.room.newMessages'))
    localStorage.removeItem('user')
  })

  it('只是往上翻了翻、没有新消息，也有回到最新的入口', async () => {
    const { container, getByTestId } = await mountRoom()
    scrollAway(getByTestId('chat-scroll'))
    await flush()
    expect(pill(container)?.textContent).toContain(t('work.room.backToLatest'))
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
    // 平滑滚动落了地：滚动事件到，位置回到最底下，提示才收起。
    Object.defineProperty(pane, 'scrollTop', { configurable: true, value: 4000 - 600, writable: true })
    pane.dispatchEvent(new Event('scroll'))
    await flush()
    expect(pill(container)).toBeNull()
  })
})
