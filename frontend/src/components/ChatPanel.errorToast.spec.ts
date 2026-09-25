// 出错提示：一次没成的事说一句就走；连不上服务器时说的是房间此刻的状态，一直留着。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component
let vuetify: ReturnType<typeof createVuetify>
let socket: {
  onmessage: ((e: { data: string }) => void) | null
  onclose: (() => void) | null
} | null = null

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 1
      onopen: (() => void) | null = null
      onmessage: ((e: { data: string }) => void) | null = null
      onclose: (() => void) | null = null
      constructor() {
        // eslint-disable-next-line @typescript-eslint/no-this-alias
        socket = this
        setTimeout(() => this.onopen?.(), 0)
      }
      close() {}
      send() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : String(url).includes('/tasks')
          ? { code: 200, data: { data: [], total: 0 } }
          : { code: 200, data: { data: [], total: 0, has_more: false } },
  }))
})

afterEach(() => {
  vi.useRealTimers()
})

async function openRoom() {
  const view = render(Panel, {
    props: { topic: { id: 't1', project_id: 'p1', title: 't1', kind: 'topic' } as Topic, showComposer: true },
    global: { plugins: [vuetify] },
  })
  await vi.advanceTimersByTimeAsync(50)
  return view
}

function frame(data: Record<string, unknown>) {
  socket!.onmessage?.({ data: JSON.stringify(data) })
}

const toastText = () => document.querySelector('.chat-error-toast')?.textContent ?? null

describe('出错提示', () => {
  it('一次没成的事说一句，过几秒自己走', async () => {
    const view = await openRoom()
    frame({ type: 'error', message: '表情更新失败' })
    await vi.advanceTimersByTimeAsync(0)
    expect(toastText()).toContain('表情更新失败')

    await vi.advanceTimersByTimeAsync(10_000)
    expect(toastText()).toBeNull()
    view.unmount()
  })

  it('新的一条顶替旧的，不排队', async () => {
    const view = await openRoom()
    frame({ type: 'error', message: '第一件事没成' })
    await vi.advanceTimersByTimeAsync(0)
    frame({ type: 'error', message: '第二件事没成' })
    await vi.advanceTimersByTimeAsync(1_000)
    expect(toastText()).toContain('第二件事没成')
    expect(toastText()).not.toContain('第一件事没成')

    await vi.advanceTimersByTimeAsync(10_000)
    expect(toastText()).toBeNull()
    view.unmount()
  })

  it('连接被拒时一直留着：房间为什么不动，只有这一行在说', async () => {
    const view = await openRoom()
    // 后端拒了之后紧接着就把连接关掉。
    frame({ type: 'error', code: 'forbidden', message: '你已不在这个项目里' })
    socket!.onclose?.()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(toastText()).toContain('你已不在这个项目里')
    view.unmount()
  })
})
