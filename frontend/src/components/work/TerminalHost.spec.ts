/**
 * A 终端 tab never shows a blank screen while it has no shell: what it is waiting for, what failed
 * and what the person can do about it are said in its place.
 *
 * The task's environment and the terminal socket are stand-ins that keep the state the real ones
 * do; the assertions are on what the tab shows and on what the stand-ins ended up holding.
 */
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const env = {
  machine: 'ready' as 'ready' | 'preparing' | null,
  failed: null as 'unavailable' | null,
  prepareFails: false,
  prepares: 0,
}

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getTaskMachineStatus: async () => ({ machine: env.machine, failed: env.failed }),
    prepareTaskMachine: async () => {
      env.prepares += 1
      if (env.prepareFails) throw new actual.ApiError(503, '任务的环境未能准备好，稍后重试')
      env.machine = 'preparing'
      env.failed = null
      return { started: true, reason: null }
    },
    taskTerminalWsUrl: (task: string, id: string) => `ws://test/${task}/${id}`,
  }
})

// xterm draws on a canvas a DOM test does not have: the screen is a stand-in that shows what the
// host puts over it and takes the shell's bytes.
vi.mock('../panels/PanelTerminal.vue', () => ({
  default: defineComponent({
    name: 'PanelTerminalStub',
    props: {
      placeholder: { type: String, default: null },
      covered: Boolean,
      note: { type: String, default: null },
      active: Boolean,
    },
    emits: ['type', 'resize'],
    setup(props, { slots, expose }) {
      expose({
        write: (_bytes: Uint8Array, done: () => void) => done(),
        reset: () => {},
        size: () => ({ cols: 80, rows: 24 }),
      })
      return () =>
        h('div', [props.covered ? h('div', slots.cover?.()) : props.placeholder ? h('p', props.placeholder) : null])
    },
  }),
}))

import TerminalHost from './TerminalHost.vue'

/** The terminal socket's far end. */
class FakeSocket {
  static all: FakeSocket[] = []
  static OPEN = 1
  readyState = 1
  binaryType = 'blob'
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor(public url: string) {
    FakeSocket.all.push(this)
  }
  send() {}
  close() {
    this.readyState = 3
  }
  say(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
}
const last = () => FakeSocket.all[FakeSocket.all.length - 1]

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})
beforeEach(() => {
  setLocale('zh-CN')
  FakeSocket.all = []
  env.machine = 'ready'
  env.failed = null
  env.prepareFails = false
  env.prepares = 0
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function mount() {
  return render(TerminalHost as unknown as Component, {
    props: { taskId: 'task-1', terminalId: 't1', active: true },
    global: { plugins: [vuetify] },
  })
}

describe('a 终端 tab with no shell to show yet', () => {
  it('says the environment is being prepared while the shell is reached, then shows the shell', async () => {
    mount()
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
    // Reaching the shell can take a while (a frozen environment is woken): never a blank screen.
    expect(screen.getByText('正在准备环境')).toBeTruthy()
    last().say({ type: 'attached', ended: false })
    await waitFor(() => expect(screen.queryByText('正在准备环境')).toBeNull())
  })

  it('brings the environment up first when none holds the task, saying so, then attaches', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    env.machine = null
    mount()
    expect(await screen.findByText('正在准备环境')).toBeTruthy()
    await waitFor(() => expect(env.prepares).toBe(1))
    env.machine = 'ready'
    await vi.advanceTimersByTimeAsync(2500)
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
  })

  it('says how long it has waited once the bring-up takes a while', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    env.machine = null
    mount()
    await screen.findByText('正在准备环境')
    await vi.advanceTimersByTimeAsync(12_000)
    expect(screen.getByText(/^已等待 1\d 秒$/)).toBeTruthy()
  })

  it('a bring-up that failed says so, and 重试 asks for the environment again', async () => {
    env.machine = null
    env.prepareFails = true
    mount()
    expect(await screen.findByText('环境未能准备好')).toBeTruthy()
    env.prepareFails = false
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(env.prepares).toBe(2))
    expect(await screen.findByText('正在准备环境')).toBeTruthy()
  })

  it('a shell that could not be reached says so, and 重试 reaches for it again', async () => {
    mount()
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
    // The door said no: trying again on its own would say no again.
    last().onclose?.({ code: 1008 })
    expect(await screen.findByText('无法连接终端')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(FakeSocket.all).toHaveLength(2))
  })

  it('a shell whose environment is gone offers to open a new one, which brings it up', async () => {
    mount()
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
    env.machine = null
    last().say({ type: 'refused', reason: 'unavailable' })
    expect(await screen.findByText('暂无终端会话')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '打开终端' }))
    expect(await screen.findByText('正在准备环境')).toBeTruthy()
    await waitFor(() => expect(env.prepares).toBe(1))
  })
})
