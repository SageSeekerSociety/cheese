// One 终端 tab against a stand-in for the terminal socket and the task's machine:
//
//   1. On a machine that holds the task, the tab attaches: what the person types reaches the
//      shell, and what the shell writes reaches the screen.
//   2. With no machine holding the task, the tab brings one up first and then attaches.
//   3. A refusal with a reason (the limit, someone else's computer) is said, not retried.
//   4. A shell whose machine went away leaves the tab waiting; using it starts a new one.
import { defineComponent, h } from 'vue'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTaskTerminal } from './useTaskTerminal'

const machine = { now: 'ready' as 'ready' | 'preparing' | null }
const prepareTaskMachine = vi.fn(async () => {
  machine.now = 'preparing'
  setTimeout(() => (machine.now = 'ready'), 0)
  return { started: true, reason: null }
})

vi.mock('@/api', () => ({
  getTaskMachineStatus: async () => ({ machine: machine.now, failed: null }),
  prepareTaskMachine: () => prepareTaskMachine(),
  taskTerminalWsUrl: (task: string, id: string) => `ws://test/${task}/${id}`,
}))

/** The terminal socket's far end: what the page sent, and a way to answer. */
class FakeSocket {
  static all: FakeSocket[] = []
  static OPEN = 1
  readyState = 1
  binaryType = 'blob'
  sent: unknown[] = []
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor(public url: string) {
    FakeSocket.all.push(this)
  }
  send(data: unknown) {
    this.sent.push(data)
  }
  close() {
    this.readyState = 3
  }
  say(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
  write(text: string) {
    this.onmessage?.({ data: new TextEncoder().encode(text).buffer })
  }
  typed(): string {
    return this.sent
      .filter((d): d is Uint8Array => d instanceof Uint8Array)
      .map((d) => new TextDecoder().decode(d))
      .join('')
  }
}

const shown: string[] = []
let terminal: ReturnType<typeof useTaskTerminal>

function mount() {
  render(
    defineComponent({
      setup() {
        terminal = useTaskTerminal(
          () => 'task-1',
          () => 't1',
          {
            output: (bytes, done) => {
              shown.push(new TextDecoder().decode(bytes))
              done()
            },
            reset: () => shown.splice(0),
            size: () => ({ cols: 80, rows: 24 }),
          }
        )
        void terminal.start()
        return () => h('p', `${terminal.state.value} ${terminal.reason.value ?? ''}`)
      },
    })
  )
}

const last = () => FakeSocket.all[FakeSocket.all.length - 1]

describe('a 终端 tab', () => {
  beforeEach(() => {
    FakeSocket.all = []
    shown.splice(0)
    machine.now = 'ready'
    prepareTaskMachine.mockClear()
    vi.stubGlobal('WebSocket', FakeSocket)
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('passes keystrokes to the shell and its output to the screen', async () => {
    mount()
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
    last().say({ type: 'attached', ended: false })
    await screen.findByText(/^open/)
    terminal.type('ls\r')
    last().write('README.md\r\n')
    expect(last().typed()).toBe('ls\r')
    expect(shown.join('')).toBe('README.md\r\n')
    // What reached the screen is acknowledged, so the machine sends more.
    expect(last().sent).toContainEqual(JSON.stringify({ type: 'ack' }))
  })

  it('brings the task machine up first when nothing holds the task', async () => {
    machine.now = null
    mount()
    await screen.findByText(/^preparing/)
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1), { timeout: 5000 })
  })

  it('says why there is no shell when the machine refuses one', async () => {
    mount()
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
    last().say({ type: 'refused', reason: 'limit' })
    await screen.findByText('refused limit')
    expect(FakeSocket.all).toHaveLength(1)
  })

  it('waits for use when the machine that held the shell is gone, then starts again', async () => {
    mount()
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
    machine.now = null
    last().say({ type: 'refused', reason: 'unavailable' })
    await screen.findByText(/^cold/)
    terminal.type('x')
    await screen.findByText(/^preparing/)
    await waitFor(() => expect(FakeSocket.all).toHaveLength(2), { timeout: 5000 })
  })
})
