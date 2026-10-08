import type { Component } from 'vue'
import type { Block, Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn(),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
}))
vi.mock('../api/messages', () => ({ postChatMessage: vi.fn() }))

import type { ChatMessageBody } from '../cx_types'

import { ApiError, listBlocks } from '../api'
import { postChatMessage } from '../api/messages'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale, t } from '@/i18n'

async function flushPromises() {
  await vi.advanceTimersByTimeAsync(0)
}

const sockets: TestSocket[] = []
class TestSocket {
  static OPEN = 1
  readyState = 1
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  drop = vi.fn()
  constructor() {
    sockets.push(this)
  }
}

function mountPanel(showComposer = false) {
  return render(ChatPanel as unknown as Component, {
    props: {
      topic: { id: crypto.randomUUID(), project_id: 'p', title: 'Recovery', kind: 'topic' } as Topic,
      showComposer,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  vi.useFakeTimers()
  sockets.length = 0
  vi.stubGlobal('WebSocket', TestSocket)
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ code: 200, data: { data: [], total: 0 } }),
    })
  )
  vi.mocked(listBlocks)
    .mockReset()
    .mockResolvedValue({ data: [], has_more: false, total: 0, oldest_id: null, has_newer: false, newest_id: null })
  vi.mocked(postChatMessage)
    .mockReset()
    .mockImplementation(async (topic, body) => stored(body, topic))
})

/** What the backend answers a send with: the message, stored, carrying the send's id. */
function stored(body: ChatMessageBody, topicId = vi.mocked(postChatMessage).mock.calls[0][0]): Block {
  return {
    id: crypto.randomUUID(),
    project_id: 'p',
    conversation_id: topicId,
    kind: 'message',
    author_type: 'participant',
    author: 'u',
    content: body.content,
    meta: { client_id: body.request_id },
    created_at: new Date().toISOString(),
  } as unknown as Block
}

/** The body of every send so far, in order. */
function sends(): ChatMessageBody[] {
  return vi.mocked(postChatMessage).mock.calls.map((call) => call[1])
}

async function type(view: ReturnType<typeof mountPanel>, text: string) {
  const textarea = view.container.querySelector('textarea') as HTMLTextAreaElement
  textarea.focus()
  await fireEvent.update(textarea, text)
  await fireEvent.keyDown(textarea, { key: 'Enter' })
  await flushPromises()
}
/** What the panel said on a socket, by frame type. */
function said(socket: TestSocket, type: string): unknown[] {
  return socket.send.mock.calls.map((call) => JSON.parse(String(call[0]))).filter((m) => m.type === type)
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('chat recovery after history errors', () => {
  it('reconnects after a socket closes and history temporarily returns 502', async () => {
    mountPanel()
    await flushPromises()
    expect(sockets).toHaveLength(1)
    sockets[0].onopen?.()
    vi.mocked(listBlocks).mockRejectedValueOnce(new ApiError(502, 'Bad Gateway'))
    sockets[0].onclose?.()
    await vi.advanceTimersByTimeAsync(1000)
    expect(listBlocks).toHaveBeenCalledTimes(2)
    expect(sockets).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(listBlocks).toHaveBeenCalledTimes(3)
    expect(sockets).toHaveLength(2)
  })

  it('a topic’s first connect does not resync what the room announces; a reconnect does', async () => {
    // The connect-time doc resync exists for a doc saved while DISCONNECTED. On a
    // topic's first connect the doc panel is already loading the room fresh, so
    // emitting it would only make the panel re-read /docs + /doc/history that it
    // just fetched. Only a re-connect (same topic, socket dropped and back) needs it.
    const view = mountPanel()
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    expect(view.emitted('state-changed'), 'first connect must not resync the doc').toBeUndefined()

    sockets[0].onclose?.()
    await vi.advanceTimersByTimeAsync(1000)
    sockets[1].onopen?.()
    await flushPromises()
    // What the room announced while the socket was down is gone: everything it
    // announces is treated as changed once, its own row by the room's id.
    const caught = view.emitted('state-changed') as unknown[][]
    expect(caught.map((args) => args[0])).toEqual(
      expect.arrayContaining(['doc', 'pins', 'accept', 'feedback', 'skills', 'threads', 'topics'])
    )
    expect(caught).toContainEqual(['topics', expect.any(String)])
  })

  it('does not retry a forbidden history response', async () => {
    vi.mocked(listBlocks).mockRejectedValue(new ApiError(403, 'Forbidden'))
    mountPanel()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(listBlocks).toHaveBeenCalledTimes(1)
    expect(sockets).toHaveLength(1)
    expect(sockets[0].close).toHaveBeenCalledOnce()
  })

  it('does not restart recovery when a pending fetch fails after unmount', async () => {
    let reject!: (error: Error) => void
    vi.mocked(listBlocks).mockReturnValueOnce(
      new Promise((_, fail) => {
        reject = fail
      })
    )
    const view = mountPanel()
    await flushPromises()
    view.unmount()
    reject(new ApiError(502, 'Bad Gateway'))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(listBlocks).toHaveBeenCalledTimes(1)
    expect(sockets).toHaveLength(1)
    expect(sockets[0].close).toHaveBeenCalledOnce()
  })

  it('shows the stored message the moment the send is answered, with no echo needed', async () => {
    const view = mountPanel(true)
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    await type(view, 'stored without an echo')
    expect(sends()).toHaveLength(1)
    expect(view.container.textContent).toContain('stored without an echo')
    expect(view.container.querySelector('.im-row--pending')).toBeNull()
  })

  it('sends again with the same request id after the send could not get through', async () => {
    vi.mocked(postChatMessage).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const view = mountPanel(true)
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    await type(view, 'not persisted yet')
    expect(sends()).toHaveLength(1)
    expect(view.container.querySelector('.im-row--pending')).not.toBeNull()

    await vi.advanceTimersByTimeAsync(1000)
    expect(sends()).toHaveLength(2)
    expect(sends()[1].request_id).toBe(sends()[0].request_id)
    expect(sends()[1].content).toBe('not persisted yet')
    expect(view.container.querySelector('.im-row--pending')).toBeNull()
  })

  it('a send stuck in transit is abandoned and sent again with the same id', async () => {
    vi.mocked(postChatMessage).mockImplementationOnce(
      (_topic, _body, signal) =>
        new Promise((_, reject) => signal?.addEventListener('abort', () => reject(new DOMException('', 'AbortError'))))
    )
    const view = mountPanel(true)
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    await type(view, 'slow line')
    await vi.advanceTimersByTimeAsync(30_000)
    await vi.advanceTimersByTimeAsync(1000)
    expect(sends()).toHaveLength(2)
    expect(sends()[1].request_id).toBe(sends()[0].request_id)
  })

  it('a message whose echo arrives while its send is still out is not shown twice', async () => {
    let answer!: (block: Block) => void
    vi.mocked(postChatMessage).mockImplementationOnce(() => new Promise((yes) => (answer = yes)))
    const view = mountPanel(true)
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    await type(view, 'echo first')
    const block = stored(sends()[0])
    sockets[0].onmessage?.({ data: JSON.stringify({ type: 'user_block', block }) })
    answer(block)
    await flushPromises()
    // Count the rendered rows, not the screen-reader live line that repeats the newest message.
    const rowsText = Array.from(view.container.querySelectorAll('[data-mid]'), (row) => row.textContent).join('')
    expect(rowsText.split('echo first')).toHaveLength(2)
    expect(view.container.querySelector('.im-row--pending')).toBeNull()
  })

  it('messages go out one at a time, in the order they were typed', async () => {
    let answer!: (block: Block) => void
    vi.mocked(postChatMessage).mockImplementationOnce(
      (topic, body) => new Promise((yes) => (answer = () => yes(stored(body, topic))))
    )
    const view = mountPanel(true)
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    await type(view, 'first')
    await type(view, 'second')
    expect(sends().map((b) => b.content)).toEqual(['first'])
    answer(undefined as unknown as Block)
    await flushPromises()
    expect(sends().map((b) => b.content)).toEqual(['first', 'second'])
  })

  it('a drop the first reconnect heals never puts up the reconnecting banner', async () => {
    // What a release does to every open room: the socket is cut, and the retry a
    // second later gets straight through. Nothing was lost; nothing to tell.
    const view = mountPanel()
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()

    sockets[0].onerror?.()
    sockets[0].onclose?.()
    await flushPromises()
    expect(view.queryByText(t('work.room.socket.reconnecting'))).toBeNull()

    await vi.advanceTimersByTimeAsync(1000)
    expect(sockets).toHaveLength(2)
    sockets[1].onopen?.()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(view.queryByText(t('work.room.socket.reconnecting'))).toBeNull()
  })

  it('a link that stays down is announced, and stays announced until it is back', async () => {
    const view = mountPanel()
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()

    sockets[0].onerror?.()
    sockets[0].onclose?.()
    // The retry's handshake goes nowhere.
    await vi.advanceTimersByTimeAsync(7_000)
    expect(sockets).toHaveLength(2)
    expect(view.queryByText(t('work.room.socket.reconnecting'))).toBeNull()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(view.getByText(t('work.room.socket.reconnecting'))).toBeTruthy()

    // Still down on the next try: the refetch before it clears the line, the
    // failed handshake puts it straight back rather than waiting all over again.
    sockets[1].onerror?.()
    sockets[1].onclose?.()
    await vi.advanceTimersByTimeAsync(2_000)
    expect(sockets).toHaveLength(3)
    sockets[2].onerror?.()
    await flushPromises()
    expect(view.getByText(t('work.room.socket.reconnecting'))).toBeTruthy()

    sockets[2].onclose?.()
    await vi.advanceTimersByTimeAsync(4_000)
    sockets[3].onopen?.()
    await flushPromises()
    expect(view.queryByText(t('work.room.socket.reconnecting'))).toBeNull()
  })

  it('pings an idle socket and replaces it when nothing answers', async () => {
    mountPanel()
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    expect(said(sockets[0], 'ping')).toEqual([])

    await vi.advanceTimersByTimeAsync(15_000)
    expect(said(sockets[0], 'ping')).toEqual([{ type: 'ping' }])

    await vi.advanceTimersByTimeAsync(10_000)
    // The link under every room is replaced, not just this room's subscription.
    expect(sockets[0].drop).toHaveBeenCalledOnce()
    expect(sockets).toHaveLength(2)
    expect(vi.mocked(listBlocks)).toHaveBeenCalledTimes(2)
  })

  it('keeps a socket whose ping is answered', async () => {
    mountPanel()
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(15_000)
    sockets[0].onmessage?.({ data: JSON.stringify({ type: 'pong' }) })
    await vi.advanceTimersByTimeAsync(10_000)
    expect(sockets[0].close).not.toHaveBeenCalled()
    expect(sockets).toHaveLength(1)

    // Ordinary traffic answers too: a frame arriving instead of the pong.
    await vi.advanceTimersByTimeAsync(5_000)
    expect(said(sockets[0], 'ping')).toHaveLength(2)
    sockets[0].onmessage?.({ data: JSON.stringify({ type: 'done' }) })
    await vi.advanceTimersByTimeAsync(10_000)
    expect(sockets[0].close).not.toHaveBeenCalled()
  })
})

it('can send while initial history is pending and preserves live messages when it arrives', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof listBlocks>>) => void
  vi.mocked(listBlocks).mockReturnValueOnce(
    new Promise((yes) => {
      resolve = yes
    })
  )
  const view = mountPanel(true)
  await flushPromises()
  expect(sockets).toHaveLength(1)
  sockets[0].onopen?.()
  await flushPromises()
  const textarea = view.container.querySelector('textarea') as HTMLTextAreaElement
  expect(textarea.disabled).toBe(false)
  await type(view, 'hello before history')
  const block = stored(sends()[0])
  sockets[0].onmessage?.({ data: JSON.stringify({ type: 'user_block', block }) })
  resolve({ data: [], has_more: false, total: 0, oldest_id: null, has_newer: false, newest_id: null })
  await flushPromises()
  expect(view.container.textContent).toContain('hello before history')
  expect(view.container.querySelector('.im-row--pending')).toBeNull()
  expect(sockets).toHaveLength(1)
})

it('a delayed history snapshot cannot restore a retracted live block', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof listBlocks>>) => void
  vi.mocked(listBlocks).mockReturnValueOnce(
    new Promise((yes) => {
      resolve = yes
    })
  )
  const view = mountPanel()
  await flushPromises()
  sockets[0].onopen?.()
  sockets[0].onmessage?.({ data: JSON.stringify({ type: 'retract_block', block_id: 'removed' }) })
  resolve({
    data: [
      {
        id: 'removed',
        kind: 'message',
        author: 'alice',
        content: 'stale removed message',
        meta: {},
        created_at: new Date().toISOString(),
      } as Block,
    ],
    has_more: false,
    total: 1,
    oldest_id: null,
    has_newer: false,
    newest_id: null,
  })
  await flushPromises()
  expect(view.container.textContent).not.toContain('stale removed message')
})

it('keeps a reaction received before its message arrives in history', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof listBlocks>>) => void
  vi.mocked(listBlocks).mockReturnValueOnce(
    new Promise((yes) => {
      resolve = yes
    })
  )
  const view = mountPanel()
  await flushPromises()
  sockets[0].onopen?.()
  sockets[0].onmessage?.({
    data: JSON.stringify({
      type: 'reaction',
      block_id: 'old',
      reactions: [{ emoji: '👍', count: 1, authors: ['alice'] }],
    }),
  })
  resolve({
    data: [
      {
        id: 'old',
        conversation_id: 'room',
        kind: 'message',
        author: 'alice',
        author_type: 'participant',
        content: 'history message',
        reactions: [],
        meta: {},
        created_at: new Date().toISOString(),
      } as Block,
    ],
    has_more: false,
    total: 1,
    oldest_id: null,
    has_newer: false,
    newest_id: null,
  })
  await flushPromises()
  expect(view.container.querySelector('.rx-emoji')?.textContent).toBe('👍')
})

it('a rejected message stops waiting and only resends when the user retries', async () => {
  vi.mocked(postChatMessage).mockRejectedValueOnce(new ApiError(403, '房间已关闭'))
  const view = mountPanel(true)
  await flushPromises()
  sockets[0].onopen?.()
  await flushPromises()
  await type(view, 'rejected message')
  expect(view.container.textContent).toContain('房间已关闭')
  expect(view.getByText(t('work.room.retry.action'))).toBeTruthy()
  sockets[0].onclose?.()
  await vi.advanceTimersByTimeAsync(1000)
  sockets[1].onopen?.()
  await flushPromises()
  expect(sends()).toHaveLength(1)
  await fireEvent.click(view.getByText(t('work.room.retry.action')))
  await flushPromises()
  expect(sends()).toHaveLength(2)
  expect(sends()[1].request_id).toBe(sends()[0].request_id)
})

it('editing a message that failed to send puts its text back in the box and takes it off the timeline', async () => {
  vi.mocked(postChatMessage).mockRejectedValueOnce(new ApiError(403, '房间已关闭'))
  const view = mountPanel(true)
  await flushPromises()
  sockets[0].onopen?.()
  await flushPromises()
  const textarea = view.container.querySelector('textarea') as HTMLTextAreaElement
  await type(view, 'the words I typed')
  expect(textarea.value).toBe('')

  await fireEvent.click(view.getByText(t('work.room.outbox.edit')))
  await flushPromises()
  expect(textarea.value).toBe('the words I typed')
  expect(view.container.querySelector('.im-row--pending')).toBeNull()
  // 拿回来编辑的那条不会在重连之后自己又发出去。
  sockets[0].onclose?.()
  await vi.advanceTimersByTimeAsync(1000)
  sockets[1].onopen?.()
  await flushPromises()
  expect(sends()).toHaveLength(1)
})

// 读历史走 HTTP，之后落下的走 socket。一条消息落在「读」和「订阅生效」之间，两头都
// 不会带它来；订阅确认时服务端说了那一刻最新的是哪条，手里没有就补读一次。
describe('a message that lands between the read and the subscription', () => {
  function message(id: string, content: string): Block {
    return {
      id,
      project_id: 'p',
      conversation_id: 'c',
      kind: 'message',
      author_type: 'participant',
      author: 'someone',
      content,
      created_at: new Date().toISOString(),
    } as unknown as Block
  }
  function page(blocks: Block[]) {
    return {
      data: blocks,
      has_more: false,
      total: blocks.length,
      oldest_id: blocks[0]?.id ?? null,
      has_newer: false,
      newest_id: blocks.at(-1)?.id ?? null,
    }
  }

  it('is read once the room is subscribed', async () => {
    const before = message('m1', '读之前就在的')
    const between = message('m2', '读完才落下的')
    vi.mocked(listBlocks)
      .mockResolvedValueOnce(page([before]))
      .mockResolvedValueOnce(page([before, between]))
    const view = mountPanel()
    await flushPromises()

    Object.assign(sockets[0], { newest: 'm2' })
    sockets[0].onopen?.()
    await flushPromises()

    expect(listBlocks).toHaveBeenCalledTimes(2)
    expect(view.container.textContent).toContain('读完才落下的')
    expect(sockets).toHaveLength(1)
  })

  it('is not looked for when the room already holds the newest message', async () => {
    vi.mocked(listBlocks).mockResolvedValueOnce(page([message('m1', '只有这一条')]))
    mountPanel()
    await flushPromises()

    Object.assign(sockets[0], { newest: 'm1' })
    sockets[0].onopen?.()
    await flushPromises()

    expect(listBlocks).toHaveBeenCalledTimes(1)
  })
})
