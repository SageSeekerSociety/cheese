// 断线期间来了不止一页：重连回来，屏幕上那一段整个换成读回来的最新一页（两段之间
// 有一截没读过，接不上）。换上来的块和原来那段一条都不重合，读的人刚才看的那条已经
// 不在屏幕上——那就该落在最新那条上，而不是照旧的像素偏移停在新内容的某个中间；
// 下次回到这个房间也一样。自己刚发的那条同理：发完紧接着整段一换，还是落在最底下。
import type { Block, Topic } from '@/cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
const postChatMessage = vi.fn()

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('@/api/pins', () => ({ listPins: async () => [], pinBlock: vi.fn(), unpinBlock: vi.fn() }))
vi.mock('@/api/threads', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/threads')>()),
  listThreads: async () => [],
}))
vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/me', () => ({ myHandle: () => 'me', myId: () => '1' }))
vi.mock('@/api/messages', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/messages')>()),
  postChatMessage: (...args: unknown[]) => postChatMessage(...args),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...args: unknown[]) => listBlocks(...args),
    getTopic: vi.fn(async (id: string) => room(id)),
  }
})

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

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
}

/** 所有 ResizeObserver 的回调：「这一帧排完版了」。 */
const observers: (() => void)[] = []
class FakeResizeObserver {
  constructor(cb: () => void) {
    observers.push(cb)
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}
function relayout() {
  for (const cb of [...observers]) cb()
}

function room(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: id,
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  } as Topic
}

/** 第 `n` 分钟说的一条。 */
function said(roomId: string, n: number, author = 'alice'): Block {
  return {
    id: `${roomId}-m${n}`,
    project_id: 'p1',
    conversation_id: roomId,
    kind: 'message',
    author_type: 'participant',
    author,
    content: `message ${n}`,
    created_at: new Date(Date.UTC(2026, 9, 7, 10, n)).toISOString(),
  } as unknown as Block
}

function minutes(roomId: string, from: number, to: number): Block[] {
  const out: Block[] = []
  for (let n = from; n <= to; n++) out.push(said(roomId, n))
  return out
}

const ROW = 100
const VIEW = 600

/**
 * happy-dom 不排版：这一栏的高度按画出来的行数算（每行 100），视口 600，
 * scrollTop 和真的一样被夹在 [0, 内容高 - 视口高] 之间。`grow` 是行数不变、
 * 已有的行换成真实高度时多出来的那截（离屏的行先按估计高度占位）。
 */
function layOut(pane: HTMLElement) {
  let top = 0
  let extra = 0
  const height = () => Math.max(pane.querySelectorAll('[data-mid]').length * ROW + extra, VIEW)
  Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => VIEW })
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, get: height })
  Object.defineProperty(pane, 'scrollTop', {
    configurable: true,
    get: () => Math.max(0, Math.min(top, height() - VIEW)),
    set: (v: number) => (top = Math.max(0, Math.min(v, height() - VIEW))),
  })
  pane.scrollTo = vi.fn()
  return {
    grow(by: number) {
      extra += by
    },
  }
}

const flush = () => vi.advanceTimersByTimeAsync(0)

function bottomOf(pane: HTMLElement) {
  return pane.scrollHeight - pane.clientHeight
}

/** 人往上翻到顶上那几条。 */
async function readUp(pane: HTMLElement) {
  pane.scrollTop = 0
  pane.dispatchEvent(new Event('scroll'))
  await flush()
}

/** 现在这条 socket 断掉，等重连读回最新一页。 */
async function dropLink() {
  FakeWebSocket.instances.at(-1)!.onclose?.()
  await vi.advanceTimersByTimeAsync(1000)
  await flush()
  relayout()
}

async function mountRoom(roomId: string) {
  const panel = ref<{ send: (content: string, summon: boolean) => boolean } | null>(null)
  const topic = ref(room(roomId))
  const view = render(
    defineComponent({
      setup: () => () =>
        h(ChatPanel, { ref: panel, topic: topic.value, topicList: [topic.value], members: [], showComposer: true }),
    }),
    { global: { plugins: [createVuetify({ components, directives }), i18n] } }
  )
  const pane = view.getByTestId('chat-scroll')
  const layout = layOut(pane)
  await flush()
  FakeWebSocket.instances.at(-1)!.onopen?.()
  await vi.advanceTimersByTimeAsync(500) // 连上之后的重放追赶期过去
  relayout()
  return { view, pane, panel, topic, layout }
}

beforeEach(() => {
  setLocale('en')
  vi.useFakeTimers()
  FakeWebSocket.instances = []
  observers.length = 0
  listBlocks.mockReset()
  postChatMessage.mockReset()
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** 断线期间来了一整页还多：读回来的这一页最老那条都比屏幕上的新，接不上。 */
function pageAfterOutage(roomId: string) {
  return { data: minutes(roomId, 40, 59), has_more: true }
}

describe('重连回来整段换成最新一页', () => {
  it('往上翻着的人落在最新那条上，不停在新内容的中间', async () => {
    const id = crypto.randomUUID()
    listBlocks.mockResolvedValue({ data: minutes(id, 1, 20), has_more: false })
    const { pane } = await mountRoom(id)
    await readUp(pane)

    listBlocks.mockResolvedValue(pageAfterOutage(id))
    await dropLink()

    expect(pane.textContent).toContain('message 59')
    expect(pane.scrollTop).toBe(bottomOf(pane))
  })

  it('停在底部的人照旧停在底部', async () => {
    const id = crypto.randomUUID()
    listBlocks.mockResolvedValue({ data: minutes(id, 1, 20), has_more: false })
    const { pane } = await mountRoom(id)
    expect(pane.scrollTop).toBe(bottomOf(pane))

    listBlocks.mockResolvedValue(pageAfterOutage(id))
    await dropLink()

    expect(pane.scrollTop).toBe(bottomOf(pane))
  })

  it('切走再回来，也回到最新那条，而不是换之前停的那个偏移', async () => {
    const id = crypto.randomUUID()
    const other = crypto.randomUUID()
    listBlocks.mockImplementation(async (roomId: string) =>
      roomId === id ? { data: minutes(id, 1, 20), has_more: false } : { data: minutes(other, 1, 3), has_more: false }
    )
    const { pane, topic } = await mountRoom(id)
    await readUp(pane)

    listBlocks.mockImplementation(async (roomId: string) =>
      roomId === id ? pageAfterOutage(id) : { data: minutes(other, 1, 3), has_more: false }
    )
    await dropLink()

    topic.value = room(other)
    await flush()
    relayout()
    topic.value = room(id)
    await flush()
    await vi.advanceTimersByTimeAsync(500)
    relayout()

    expect(pane.textContent).toContain('message 59')
    expect(pane.scrollTop).toBe(bottomOf(pane))
  })
})

describe('发完一条紧接着整段一换', () => {
  it('发的人落在最底下', async () => {
    const id = crypto.randomUUID()
    listBlocks.mockResolvedValue({ data: minutes(id, 1, 20), has_more: false })
    const { pane, panel } = await mountRoom(id)
    await readUp(pane)

    // 发出去的那条在断线期间才落库：读回来的最新一页里有它，跟屏幕上那段接不上。
    const mine = { ...said(id, 60, 'me'), content: 'my words' }
    postChatMessage.mockReturnValue(new Promise(() => {}))
    expect(panel.value!.send('my words', false)).toBe(true)
    await flush()
    relayout()
    expect(pane.scrollTop).toBe(bottomOf(pane))

    listBlocks.mockResolvedValue({ data: [...minutes(id, 40, 59), mine], has_more: true })
    await dropLink()

    expect(pane.textContent).toContain('my words')
    expect(pane.scrollTop).toBe(bottomOf(pane))
  })

  // 发完，上面离屏的几行换成真实高度、收短了，下面的回复同时还在长：浏览器的滚动锚定
  // 把 scrollTop 往上挪、发一个 scroll 事件，ResizeObserver 晚一步才看到。那个事件
  // 不是人往上翻，发的人仍该落在最底下。
  it('发完内容紧接着重排，也还是落在最底下', async () => {
    const id = crypto.randomUUID()
    listBlocks.mockResolvedValue({ data: minutes(id, 1, 20), has_more: false })
    const { pane, panel, layout } = await mountRoom(id)
    await readUp(pane)

    postChatMessage.mockReturnValue(new Promise(() => {}))
    panel.value!.send('my words', false)
    await flush()
    relayout()
    const sentAt = pane.scrollTop
    expect(sentAt).toBe(bottomOf(pane))

    layout.grow(-400) // 上面收短了 400
    layout.grow(300) // 下面同时长出来 300
    pane.scrollTop = sentAt - 400 // 锚定把位置往上挪了 400
    pane.dispatchEvent(new Event('scroll'))
    await flush()
    relayout()

    expect(pane.scrollTop).toBe(bottomOf(pane))
  })
})
