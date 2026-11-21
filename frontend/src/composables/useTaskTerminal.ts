// One 终端 tab of a task: the person's shell by the id the tab was given, on the task's machine.
//
// The shell is the machine's (backend `machine/terminals`), not this tab's: closing the page
// leaves it running, and the tab opened again shows what it wrote. Opening the tab brings the
// task's machine up first when nothing holds the task's copy (`prepareTaskMachine`, as the 文件
// panel's first edit does), then attaches over the terminal socket (`taskTerminalWsUrl`):
// BINARY frames are the shell's bytes both ways, TEXT frames are what happened (down:
// `attached`, `exit`, `refused`; up: `resize`, and `ack` once a chunk of output is on screen,
// which is what lets the machine send more).
import { onBeforeUnmount, ref } from 'vue'
import { useEventListener } from '@vueuse/core'

import { getTaskMachineStatus, prepareTaskMachine, taskTerminalWsUrl } from '@/api'

/**
 * Where the tab is:
 * - `preparing`: the task's machine is being brought up;
 * - `connecting`: reaching the shell;
 * - `open`: attached, typing goes to the shell;
 * - `ended`: the shell exited;
 * - `cold`: the machine that held the shell is gone; using the tab starts a new one;
 * - `refused`: no shell here, for `reason`;
 * - `failed`: the shell could not be reached.
 */
export type TaskTerminalState = 'preparing' | 'connecting' | 'open' | 'ended' | 'cold' | 'refused' | 'failed'

/** Why a tab has no shell, as the backend says it. */
export type TaskTerminalReason = 'not_owner' | 'unsupported' | 'outdated' | 'limit'

export interface TaskTerminalHooks {
  /** The shell's bytes; call `done` once they are on screen. */
  output: (bytes: Uint8Array, done: () => void) => void
  /** What follows starts again from what the shell last wrote: clear the screen first. */
  reset: () => void
  /** The terminal's size now, to open and to resize the shell at. */
  size: () => { cols: number; rows: number }
}

/** How often the machine's bring-up is asked about (ms). */
const POLL_MS = 2000
/** How many answers of "nothing is being brought up" are waited out before it counts as failed. */
const IDLE_POLLS = 10
/** How a dropped socket is tried again (ms): a release of the process serving it drops it. */
const RETRY_MS = [500, 1000, 2000, 4000, 8000]
const REASONS: ReadonlySet<string> = new Set(['not_owner', 'unsupported', 'outdated', 'limit'])

export function useTaskTerminal(taskId: () => string, terminalId: () => string, hooks: TaskTerminalHooks) {
  const state = ref<TaskTerminalState>('connecting')
  const reason = ref<TaskTerminalReason | null>(null)
  let socket: WebSocket | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let retries = 0
  let stopped = false
  const encoder = new TextEncoder()

  function clearTimer() {
    if (timer) clearTimeout(timer)
    timer = null
  }

  function drop() {
    if (!socket) return
    socket.onclose = null
    socket.onmessage = null
    socket.close()
    socket = null
  }

  /** Bring the task's machine up when nothing holds its copy, then attach. */
  async function start() {
    if (stopped) return
    clearTimer()
    reason.value = null
    try {
      const now = await getTaskMachineStatus(taskId())
      if (now.machine === 'ready') return connect()
      state.value = 'preparing'
      if (now.machine !== 'preparing') await prepareTaskMachine(taskId())
      watchBringUp(0)
    } catch {
      state.value = 'failed'
    }
  }

  function watchBringUp(idle: number) {
    timer = setTimeout(async () => {
      timer = null
      if (stopped) return
      try {
        const now = await getTaskMachineStatus(taskId())
        if (now.machine === 'ready') return connect()
        if (now.failed || (now.machine === null && idle + 1 > IDLE_POLLS)) {
          state.value = 'failed'
          return
        }
        watchBringUp(now.machine === null ? idle + 1 : 0)
      } catch {
        watchBringUp(idle)
      }
    }, POLL_MS)
  }

  function connect() {
    if (stopped) return
    drop()
    state.value = state.value === 'open' ? 'open' : 'connecting'
    const { cols, rows } = hooks.size()
    const ws = new WebSocket(taskTerminalWsUrl(taskId(), terminalId(), cols, rows))
    ws.binaryType = 'arraybuffer'
    socket = ws
    let settled = false
    ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        const bytes = new Uint8Array(event.data)
        hooks.output(bytes, () => {
          if (socket === ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ack' }))
        })
        return
      }
      let message: { type?: string; ended?: boolean; reason?: string }
      try {
        message = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (message.type === 'attached') {
        settled = true
        retries = 0
        hooks.reset()
        state.value = message.ended ? 'ended' : 'open'
        resize()
      } else if (message.type === 'exit') {
        state.value = 'ended'
      } else if (message.type === 'refused') {
        settled = true
        void refused(message.reason ?? '')
      }
    }
    ws.onclose = (event: CloseEvent) => {
      if (socket !== ws) return
      socket = null
      if (stopped || state.value === 'refused' || state.value === 'cold' || state.value === 'failed') return
      // The door said no: trying again says no again.
      if (event.code === 1008 || event.code === 4401) {
        state.value = 'failed'
        return
      }
      if (state.value === 'ended' && settled) return
      if (retries >= RETRY_MS.length) {
        state.value = 'failed'
        return
      }
      timer = setTimeout(() => void start(), RETRY_MS[retries++])
    }
  }

  async function refused(said: string) {
    drop()
    if (REASONS.has(said)) {
      reason.value = said as TaskTerminalReason
      state.value = 'refused'
      return
    }
    // No shell could be reached. The machine that held it may be gone since (destroyed or
    // parked): then the tab waits to be used again, and a new shell starts on the next machine.
    try {
      const now = await getTaskMachineStatus(taskId())
      state.value = now.machine === null ? 'cold' : 'failed'
    } catch {
      state.value = 'failed'
    }
  }

  /** Keystrokes for the shell; in a tab whose machine is gone, the first one brings it back. */
  function type(data: string) {
    if (state.value === 'cold') {
      void start()
      return
    }
    if (state.value === 'open' && socket?.readyState === WebSocket.OPEN) socket.send(encoder.encode(data))
  }

  function resize() {
    if (socket?.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ type: 'resize', ...hooks.size() }))
  }

  // Back online: a socket that dropped while offline is tried again at once.
  useEventListener(window, 'online', () => {
    if (state.value === 'failed' && !reason.value) {
      retries = 0
      void start()
    }
  })

  onBeforeUnmount(() => {
    stopped = true
    clearTimer()
    drop()
  })

  return { state, reason, start, type, resize }
}
