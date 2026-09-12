import type { Component } from 'vue'
import type { Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
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
  close() {}
  constructor() {
    sockets.push(this)
  }
}

function mountPanel() {
  return render(ChatPanel as unknown as Component, {
    props: { topic: { id: crypto.randomUUID(), project_id: 'p', title: 'Recovery', kind: 'topic' } as Topic },
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
    expect(sockets).toHaveLength(0)
  })

  it('does not restart recovery when a pending fetch fails after unmount', async () => {
    let reject!: (error: Error) => void
    vi.mocked(listBlocks).mockReturnValueOnce(
      new Promise((_, fail) => {
        reject = fail
      })
    )
    const view = mountPanel()
    view.unmount()
    reject(new ApiError(502, 'Bad Gateway'))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(listBlocks).toHaveBeenCalledTimes(1)
    expect(sockets).toHaveLength(0)
  })
})
