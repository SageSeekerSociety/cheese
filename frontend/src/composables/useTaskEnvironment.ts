// The environment a task's work is in, as the task page knows it: one state per task, whichever
// surface of the page asks — the 文件 panel, a 终端 tab, the 搜索 column, the 改动 view.
//
// This is the only thing on the page that asks the backend how far the environment got
// (`machine/status`) and asks for it (`machine/prepare`). The surfaces read the state from here
// and tell it what their own requests learned: a save or a search answered as being prepared
// (`MachinePreparing`), a read that came back off the forge or as released (the environment is
// gone), one that the environment did not answer (`EnvironmentNotResponding`). A surface that
// believed the environment ready learns from here that it went away.
//
// Looking never starts the environment; doing does (an edit, a terminal, a search), and every
// one of them asks through `bringUp`, so one bring-up serves them all. While one is under way the
// status is asked every `POLL_MS`, and only while a surface of that task is mounted.
import { computed, onBeforeUnmount, reactive, watch } from 'vue'

import { ApiError, getTaskMachineStatus, prepareTaskMachine } from '../api'

/**
 * - `unknown`: not asked yet;
 * - `absent`: no environment holds the task's copy, and nobody asked for one;
 * - `preparing`: one is being brought up;
 * - `ready`: one holds the copy;
 * - `unanswered`: the one holding it did not answer;
 * - `failed`: bringing one up did not finish.
 */
export type TaskEnvironmentState = 'unknown' | 'absent' | 'preparing' | 'ready' | 'unanswered' | 'failed'

/** How often a bring-up is asked about (ms). */
export const POLL_MS = 2000
/** How many answers of "nothing is being brought up" are waited out before it counts as failed. */
const IDLE_POLLS = 10

interface Entry {
  state: TaskEnvironmentState
  /** When the bring-up under way started (ms since the epoch), for how long it has waited. */
  since: number | null
  /** What the backend said when the bring-up failed or the environment did not answer. */
  detail: string
  /** Surfaces of this task on screen. */
  users: number
  idlePolls: number
}

const entries = reactive(new Map<string, Entry>())
/** The next status read of a bring-up under way, by task (kept out of the reactive state). */
const polls = new Map<string, ReturnType<typeof setTimeout>>()
/** A status read in flight, by task, shared by whoever asks meanwhile. */
const asking = new Map<string, Promise<void>>()

function entry(tid: string): Entry {
  let found = entries.get(tid)
  if (!found) {
    entries.set(tid, {
      state: 'unknown',
      since: null,
      detail: '',
      users: 0,
      idlePolls: 0,
    })
    found = entries.get(tid)!
  }
  return found
}

function set(tid: string, state: TaskEnvironmentState, detail = '') {
  const e = entry(tid)
  if (state === 'preparing' && e.state !== 'preparing') e.since = Date.now()
  if (state !== 'preparing') e.since = null
  e.state = state
  e.detail = detail
  if (state === 'preparing') watchBringUp(tid)
  else stopPolling(tid)
}

function stopPolling(tid: string) {
  clearTimeout(polls.get(tid))
  polls.delete(tid)
}

function watchBringUp(tid: string) {
  const e = entry(tid)
  if (polls.has(tid) || e.users === 0) return
  e.idlePolls = 0
  polls.set(
    tid,
    setTimeout(() => void poll(tid), POLL_MS)
  )
}

async function poll(tid: string) {
  const e = entries.get(tid)
  polls.delete(tid)
  if (!e || e.users === 0 || e.state !== 'preparing') return
  try {
    const now = await getTaskMachineStatus(tid)
    if (entries.get(tid) !== e || e.state !== 'preparing') return
    if (now.machine === 'ready') return set(tid, 'ready')
    if (now.failed === 'unavailable' || (now.machine === null && ++e.idlePolls > IDLE_POLLS)) {
      return set(tid, 'failed')
    }
  } catch {
    // A status that did not answer is asked again.
  }
  if (e.users > 0 && !polls.has(tid) && e.state === 'preparing') {
    polls.set(
      tid,
      setTimeout(() => void poll(tid), POLL_MS)
    )
  }
}

/** Ask once how far the environment got; `fresh` asks even when it is believed ready. */
async function check(tid: string, fresh = false): Promise<TaskEnvironmentState> {
  const e = entry(tid)
  if (e.state === 'preparing' || (e.state === 'ready' && !fresh)) return e.state
  let pending = asking.get(tid)
  if (!pending) {
    pending = (async () => {
      try {
        const now = await getTaskMachineStatus(tid)
        if (entries.get(tid) !== e) return
        if (now.machine === 'ready') set(tid, 'ready')
        else if (now.machine === 'preparing') set(tid, 'preparing')
        else if (now.failed === 'unavailable') set(tid, 'failed')
        else if (e.state !== 'unanswered' && e.state !== 'failed') set(tid, 'absent')
      } catch {
        // Not known: whoever asked decides what to do without it.
      } finally {
        asking.delete(tid)
      }
    })()
    asking.set(tid, pending)
  }
  await pending
  return e.state
}

/** Ask for the environment, unless a bring-up is under way already. */
async function bringUp(tid: string): Promise<void> {
  const e = entry(tid)
  if (e.state === 'preparing') return
  set(tid, 'preparing')
  try {
    await prepareTaskMachine(tid)
  } catch (error) {
    if (entries.get(tid) === e) set(tid, 'failed', error instanceof Error ? error.message : '')
  }
}

/**
 * What a request of a surface learned about the environment. Answers whether the refusal was the
 * environment's own condition, which the surface shows through this state rather than as its own
 * error: being prepared, not answering, failed to prepare. A released environment is learned too,
 * but its sentence (what became of the work) stays the surface's to say.
 */
function learn(tid: string, error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  switch (error.code) {
    case 'MachinePreparing':
      set(tid, 'preparing')
      return true
    case 'EnvironmentNotResponding':
      set(tid, 'unanswered', error.message)
      return true
    case 'MachinePrepareFailed':
      set(tid, 'failed', error.message)
      return true
    case 'EnvironmentReleased':
    case 'SearchNeedsEnvironment':
      if (entry(tid).state !== 'preparing') set(tid, 'absent')
      return false
    default:
      return false
  }
}

/** A request was answered by the environment: it is there. */
function sawReady(tid: string) {
  if (entry(tid).state !== 'ready') set(tid, 'ready')
}

/** A request was answered off the forge though the environment was believed ready: it is gone. */
function sawGone(tid: string) {
  if (entry(tid).state === 'ready') set(tid, 'absent')
}

function join(tid: string) {
  const e = entry(tid)
  e.users += 1
  if (e.state === 'preparing') watchBringUp(tid)
}

function leave(tid: string) {
  const e = entries.get(tid)
  if (!e || --e.users > 0) return
  stopPolling(tid)
  entries.delete(tid)
}

/** The task's environment state, for code that keeps its own per-task state at module level. */
export function taskEnvironmentState(tid: string): TaskEnvironmentState {
  return entries.get(tid)?.state ?? 'unknown'
}

export const taskEnvironment = { check, bringUp, learn, sawReady, sawGone }

/** The task's environment, for one surface of the task page: counted as on screen while mounted. */
export function useTaskEnvironment(taskId: () => string | null) {
  let joined: string | null = null
  watch(
    taskId,
    (tid) => {
      if (joined) leave(joined)
      joined = tid
      if (tid) join(tid)
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    if (joined) leave(joined)
    joined = null
  })

  const current = computed(() => {
    const tid = taskId()
    return tid ? entries.get(tid) ?? null : null
  })
  const run = <T>(work: (tid: string) => T, otherwise: T): T => {
    const tid = taskId()
    return tid ? work(tid) : otherwise
  }

  return {
    state: computed<TaskEnvironmentState>(() => current.value?.state ?? 'unknown'),
    /** When the bring-up under way started (ms since the epoch); null when none is. */
    since: computed(() => current.value?.since ?? null),
    /** What the backend said when it failed or did not answer. */
    detail: computed(() => current.value?.detail ?? ''),
    check: (fresh = false) => run((tid) => check(tid, fresh), Promise.resolve<TaskEnvironmentState>('unknown')),
    bringUp: () => run((tid) => bringUp(tid), Promise.resolve()),
    /** Ask for it again after it failed or did not answer. */
    retry: () =>
      run((tid) => {
        set(tid, 'unknown')
        return bringUp(tid)
      }, Promise.resolve()),
    learn: (error: unknown) => run((tid) => learn(tid, error), false),
    sawReady: () => run((tid) => sawReady(tid), undefined),
    sawGone: () => run((tid) => sawGone(tid), undefined),
  }
}

export type TaskEnvironment = ReturnType<typeof useTaskEnvironment>
