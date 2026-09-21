import type { Component } from 'vue'
import type { Block, Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn(),
  getProgress: vi.fn().mockResolvedValue({ items: [] }),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
  chatWsUrl: () => 'ws://test/chat',
}))

import { ApiError, listBlocks } from '../api'

import ChatPanel from './ChatPanel.vue'

async function flushPromises() {
  await vi.advanceTimersByTimeAsync(0)
}

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

function mountPanel(showComposer = false) {
  return render(ChatPanel as unknown as Component, {
    props: {
      topic: { id: crypto.randomUUID(), project_id: 'p', title: 'Recovery', kind: 'topic' } as Topic,
      showComposer,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
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
  vi.mocked(listBlocks).mockReset().mockResolvedValue({ data: [], has_more: false, total: 0, oldest_id: null })
})
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

  it('reconciles a lost echo from history before opening a replacement socket', async () => {
    const view = mountPanel(true)
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    const textarea = view.container.querySelector('textarea') as HTMLTextAreaElement
    textarea.focus()
    await fireEvent.update(textarea, 'persisted while the echo was lost')
    await fireEvent.keyDown(textarea, { key: 'Enter' })
    const sent = JSON.parse(String(sockets[0].send.mock.calls[0][0]))
    vi.mocked(listBlocks).mockResolvedValueOnce({
      data: [
        {
          id: crypto.randomUUID(),
          project_id: 'p',
          topic_id: 't',
          kind: 'message',
          author_type: 'participant',
          author: 'u',
          content: sent.content,
          meta: { client_id: sent.client_id },
          created_at: new Date().toISOString(),
        } as unknown as Block,
      ],
      has_more: false,
      total: 1,
      oldest_id: null,
    })

    await vi.advanceTimersByTimeAsync(30_000)
    expect(sockets[0].close).toHaveBeenCalledOnce()
    expect(sockets).toHaveLength(2)
    expect(view.container.querySelector('.im-row--pending')).toBeNull()
    sockets[1].onopen?.()
    expect(sockets[1].send).not.toHaveBeenCalled()
  })

  it('resends the same client id after history confirms the message is absent', async () => {
    const view = mountPanel(true)
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    const textarea = view.container.querySelector('textarea') as HTMLTextAreaElement
    textarea.focus()
    await fireEvent.update(textarea, 'not persisted yet')
    await fireEvent.keyDown(textarea, { key: 'Enter' })
    const first = JSON.parse(String(sockets[0].send.mock.calls[0][0]))

    await vi.advanceTimersByTimeAsync(30_000)
    expect(sockets).toHaveLength(2)
    sockets[1].onopen?.()
    const retried = JSON.parse(String(sockets[1].send.mock.calls[0][0]))
    expect(retried.client_id).toBe(first.client_id)
    expect(retried.content).toBe(first.content)
  })

  it('pings an idle socket and replaces it when nothing answers', async () => {
    mountPanel()
    await flushPromises()
    sockets[0].onopen?.()
    await flushPromises()
    expect(sockets[0].send).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(15_000)
    expect(JSON.parse(String(sockets[0].send.mock.calls[0][0]))).toEqual({ type: 'ping' })

    await vi.advanceTimersByTimeAsync(10_000)
    expect(sockets[0].close).toHaveBeenCalledOnce()
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
    expect(sockets[0].send).toHaveBeenCalledTimes(2)
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
  textarea.focus()
  await fireEvent.update(textarea, 'hello before history')
  await fireEvent.keyDown(textarea, { key: 'Enter' })
  const sent = JSON.parse(String(sockets[0].send.mock.calls[0][0]))
  const block = {
    id: 'live',
    topic_id: 'room',
    kind: 'message',
    author_type: 'participant',
    author: 'alice',
    content: sent.content,
    meta: { client_id: sent.client_id },
    created_at: new Date().toISOString(),
  } as Block
  sockets[0].onmessage?.({ data: JSON.stringify({ type: 'user_block', block }) })
  resolve({ data: [], has_more: false, total: 0, oldest_id: null })
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
        topic_id: 'room',
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
  })
  await flushPromises()
  expect(view.container.querySelector('.rx-emoji')?.textContent).toBe('👍')
})
