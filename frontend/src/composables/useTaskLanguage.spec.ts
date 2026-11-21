// A task's language server on the page, against a stand-in for its socket:
//
//   1. Every cell of a task shares one connection per server, and none is opened while the
//      task's machine is not up.
//   2. A refusal is not asked again until the machine becomes ready again.
//   3. A dropped socket is tried again, further apart each time, then given up on until the
//      page comes back online.
import type { LspClient } from '../lib/lsp/client'
import type { TaskMachineState } from './useTaskFiles'

import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeLanguageServer } from '../test/fakeLanguageServer'

import { RETRY_MS, useTaskLanguage } from './useTaskLanguage'

vi.mock('@/api', () => ({
  taskLanguageWsUrl: (task: string, language: string) => `ws://test/${task}/${language}`,
}))

/** The language socket's far end: the first frame says ready or refused, then LSP bytes. */
class FakeSocket {
  static all: FakeSocket[] = []
  static OPEN = 1
  readyState = 1
  binaryType = 'blob'
  server = new FakeLanguageServer()
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor(public url: string) {
    FakeSocket.all.push(this)
    this.server.transport.onData((bytes) => this.onmessage?.({ data: bytes.slice().buffer }))
  }
  send(data: unknown) {
    if (data instanceof Uint8Array) this.server.transport.send(data)
  }
  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    queueMicrotask(() => this.onclose?.({ code: 1000 }))
  }
  ready(root = '/work') {
    this.onmessage?.({ data: JSON.stringify({ type: 'ready', root }) })
  }
  refuse(reason: string) {
    this.onmessage?.({ data: JSON.stringify({ type: 'refused', reason }) })
    this.close()
  }
  /** The network goes: the socket closes with nobody having said anything. */
  drop() {
    this.readyState = 3
    this.onclose?.({ code: 1006 })
  }
}

let tasks = 0
const flush = async () => {
  for (let i = 0; i < 5; i += 1) await Promise.resolve()
}

/** A cell of the task with a C file open; what the server gave it, latest last. */
function cell(taskId: string, machine: { value: TaskMachineState }) {
  const clients: (LspClient | null)[] = []
  render(
    defineComponent({
      setup() {
        const { link } = useTaskLanguage({
          taskId: () => taskId,
          machine: () => machine.value,
          open: () => {},
        })
        link.value!.serverFor('c')!.use((client) => clients.push(client))
        return () => h('i')
      },
    })
  )
  return clients
}

const sockets = (taskId: string) => FakeSocket.all.filter((s) => s.url.includes(`/${taskId}/`))

describe("a task's language server on the page", () => {
  beforeEach(() => {
    FakeSocket.all = []
    vi.stubGlobal('WebSocket', FakeSocket)
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('is one connection shared by every cell of the task, opened only once the machine is up', async () => {
    const task = `t${++tasks}`
    const machine = ref<TaskMachineState>('unknown')
    const first = cell(task, machine)
    const second = cell(task, machine)
    await flush()
    expect(sockets(task)).toHaveLength(0)

    machine.value = 'ready'
    await nextTick()
    expect(sockets(task)).toHaveLength(1)
    expect(sockets(task)[0].url).toBe(`ws://test/${task}/c`)
    sockets(task)[0].ready('/home/me/repo')
    await vi.waitFor(() => expect(first.at(-1)).toBeTruthy())
    expect(second.at(-1)).toBe(first.at(-1))
    expect(first.at(-1)!.root).toBe('/home/me/repo')
    expect(sockets(task)).toHaveLength(1)
  })

  it('does not ask again after a refusal until the machine becomes ready again', async () => {
    vi.useFakeTimers()
    const task = `t${++tasks}`
    const machine = ref<TaskMachineState>('ready')
    const clients = cell(task, machine)
    await nextTick()
    sockets(task)[0].refuse('limit')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(sockets(task)).toHaveLength(1)
    expect(clients.filter(Boolean)).toHaveLength(0)

    machine.value = 'preparing'
    await nextTick()
    machine.value = 'ready'
    await nextTick()
    expect(sockets(task)).toHaveLength(2)
  })

  it('tries a dropped socket again with growing waits, gives up, and tries once more when back online', async () => {
    vi.useFakeTimers()
    const task = `t${++tasks}`
    const machine = ref<TaskMachineState>('ready')
    cell(task, machine)
    await nextTick()
    expect(sockets(task)).toHaveLength(1)

    for (const [i, wait] of RETRY_MS.entries()) {
      sockets(task)[i].drop()
      await vi.advanceTimersByTimeAsync(wait - 1)
      expect(sockets(task)).toHaveLength(i + 1)
      await vi.advanceTimersByTimeAsync(1)
      expect(sockets(task)).toHaveLength(i + 2)
    }
    sockets(task).at(-1)!.drop()
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(sockets(task)).toHaveLength(RETRY_MS.length + 1)

    window.dispatchEvent(new Event('online'))
    await nextTick()
    expect(sockets(task)).toHaveLength(RETRY_MS.length + 2)
  })

  it('tells the cells when a server that was up goes away', async () => {
    const task = `t${++tasks}`
    const machine = ref<TaskMachineState>('ready')
    const clients = cell(task, machine)
    await nextTick()
    sockets(task)[0].ready()
    await vi.waitFor(() => expect(clients.at(-1)).toBeTruthy())
    sockets(task)[0].drop()
    await flush()
    expect(clients.at(-1)).toBeNull()
  })
})
