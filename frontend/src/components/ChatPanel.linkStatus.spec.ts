import type { Component } from 'vue'
import type { Topic } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn().mockResolvedValue({
    data: [],
    has_more: false,
    total: 0,
    oldest_id: null,
    has_newer: false,
    newest_id: null,
  }),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
  chatWsUrl: () => 'ws://test/chat',
}))

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

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
  constructor() {
    sockets.push(this)
  }
}

function mountPanel() {
  return render(ChatPanel as unknown as Component, {
    props: {
      topic: { id: crypto.randomUUID(), project_id: 'p', title: 'Room', kind: 'topic' } as Topic,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

/** Does the panel tell the reader the room is not connected? */
function saysDisconnected(view: ReturnType<typeof mountPanel>): boolean {
  return view.container.querySelector('[title="未连接"]') !== null
}

async function wait(ms: number) {
  await vi.advanceTimersByTimeAsync(ms)
}

beforeEach(() => {
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
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('what the room says about its link', () => {
  it('does not call a room disconnected while its link is still being opened', async () => {
    const view = mountPanel()
    await wait(3000)

    expect(sockets).toHaveLength(1)
    expect(saysDisconnected(view)).toBe(false)

    sockets[0].onopen?.()
    await wait(0)
    expect(saysDisconnected(view)).toBe(false)
  })

  it('says so once the link has stayed down', async () => {
    const view = mountPanel()
    await wait(0)
    sockets[0].onopen?.()
    await wait(0)

    sockets[0].onclose?.()
    await wait(30_000)

    expect(saysDisconnected(view)).toBe(true)
  })

  it('does not report a drop the first reconnect heals', async () => {
    const view = mountPanel()
    await wait(0)
    sockets[0].onopen?.()
    await wait(0)

    sockets[0].onclose?.()
    await wait(1500)
    expect(saysDisconnected(view)).toBe(false)

    expect(sockets.length).toBeGreaterThan(1)
    sockets[sockets.length - 1].onopen?.()
    await wait(10_000)
    expect(saysDisconnected(view)).toBe(false)
  })
})
