// One 终端 tab of a task: the person's shell by the id the tab was given, on the task's machine.
//
// The shell is the machine's (backend `machine/terminals`), not this tab's: closing the page
// leaves it running, and the tab opened again shows what it wrote. Opening the tab brings the
// task's environment up first when nothing holds the task's copy, through the page's one state of
// it (`useTaskEnvironment`, as the 文件 panel's first edit and a search do), then attaches over the
// terminal socket (`taskTerminalWsUrl`):
// BINARY frames are the shell's bytes both ways, TEXT frames are what happened (down:
// `attached`, `exit`, `refused`; up: `resize`, and `ack` once a chunk of output is on screen,
// which is what lets the machine send more).
import { onBeforeUnmount, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { useTaskEnvironment } from './useTaskEnvironment'

import { taskTerminalWsUrl } from '@/api'

/**
 * Where the tab is:
 * - `preparing`: the task's environment is being brought up (or failed to be: `environment` says);
 * - `connecting`: reaching the shell, which wakes an environment that was frozen;
 * - `open`: attached, typing goes to the shell;
 * - `ended`: the shell exited;
 * - `cold`: the environment that held the shell is gone; `start` opens a new one;
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
  /** What names the tab: the shell's number, and the program in its foreground (null at the prompt). */
  named?: (name: { number?: number; program: string | null }) => void
}

/** How a dropped socket is tried again (ms): a release of the process serving it drops it. */
const RETRY_MS = [500, 1000, 2000, 4000, 8000]
const REASONS: ReadonlySet<string> = new Set(['not_owner', 'unsupported', 'outdated', 'limit'])

export function useTaskTerminal(taskId: () => string, terminalId: () => string, hooks: TaskTerminalHooks) {
  const state = ref<TaskTerminalState>('connecting')
  const reason = ref<TaskTerminalReason | null>(null)
  /** When the tab started reaching its shell (ms since the epoch). */
  const connectingSince = ref<number | null>(null)
  const environment = useTaskEnvironment(taskId)
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

  /** Bring the task's environment up when nothing holds its copy, then attach. */
  async function start() {
    if (stopped) return
    clearTimer()
    reason.value = null
    const now = await environment.check()
    if (stopped) return
    if (now === 'ready') return connect()
    state.value = 'preparing'
    // One that failed or did not answer is asked for again: opening the tab is asking.
    if (now !== 'preparing') await environment.bringUp()
  }

  // The environment came up: the tab waiting for it attaches.
  watch(
    () => environment.state.value,
    (now) => {
      if (now === 'ready' && state.value === 'preparing') connect()
    }
  )

  /** The 「重试」 after the shell could not be reached, or the environment did not come up. */
  function retry() {
    retries = 0
    if (state.value === 'preparing') void environment.retry()
    else void start()
  }

  function connect() {
    if (stopped) return
    drop()
    if (state.value !== 'open' && state.value !== 'connecting') connectingSince.value = Date.now()
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
      let message: {
        type?: string
        ended?: boolean
        reason?: string
        number?: number
        program?: string | null
        name?: string | null
      }
      try {
        message = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (message.type === 'attached') {
        settled = true
        retries = 0
        hooks.reset()
        hooks.named?.({ number: message.number, program: message.program ?? null })
        state.value = message.ended ? 'ended' : 'open'
        resize()
      } else if (message.type === 'program') {
        hooks.named?.({ program: message.name ?? null })
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
    // No shell could be reached. The environment that held it may be gone since (destroyed or
    // parked): it is asked afresh, so the whole page learns it, and the tab offers to open a new
    // shell, which starts on the next environment.
    const now = await environment.check(true)
    if (stopped) return
    state.value = now === 'absent' ? 'cold' : 'failed'
  }

  /** Keystrokes for the shell. */
  function type(data: string) {
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

  return { state, reason, connectingSince, environment, start, retry, type, resize }
}
