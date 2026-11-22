// A task's「文件」panel: the task's working copy, the one its AI teammate works
// in, browsed and edited by a person.
//
// Viewing never needs the task's machine: the listing and a file are read as
// the backend has them (off a machine, or off the forge while none holds the
// copy, then `editable: false`). Editing does. The first edit asks for the
// machine in the background (`prepareTaskMachine`) and the person keeps
// typing; their text is held in the browser (`lib/heldEdits`) with the version
// it was made against, and saved once the machine is ready. A save that finds
// the file changed underneath is merged by the backend against that version;
// when it cannot be, the person's text stays and they choose.
//
// A file can be open in several cells at once (one file per cell). What is
// known about one file of a task — its text, whether it is held, saving or in
// conflict — and about the task's machine is kept once, here at module level,
// and every cell showing it reads that one copy: two cells on the same file
// show the same text, and it is saved once.
import type { WorkspaceFile } from '../cx_types'
import type { MergeConflict } from '../types/reviewComment'

import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import {
  ApiError,
  arrangeTaskFile,
  getTaskMachineStatus,
  listFiles,
  prepareTaskMachine,
  readFile,
  writeFile,
} from '../api'
import { noteSaved } from '../lib/handIn'
import { heldEdit, heldOnlyInMemory, heldPaths, holdEdit, releaseEdit } from '../lib/heldEdits'

import { t } from '@/i18n'

/** Where the task's machine is, as far as this panel knows. */
export type TaskMachineState = 'unknown' | 'ready' | 'preparing' | 'failed'

/** The file open in the panel. */
export interface OpenTaskFile {
  path: string
  /** What the editor shows: the person's text once they have typed. */
  content: string
  /** The text `version` names: what the person's edits are made against. */
  base: string
  version: string | null
  bytes: number
  binary: boolean
  tooLarge: boolean
}

export type TaskFileArrangement =
  | { operation: 'create' | 'mkdir' | 'delete'; path: string }
  | { operation: 'move'; path: string; to: string }

export interface TaskFilesOptions {
  projectId: () => string | null
  taskId: () => string | null
  /** The person may not change this task's files (task ended, not theirs to edit). */
  readOnly: () => boolean
  /** The panel is on screen: the listing is read the first time it is. */
  active: () => boolean
  /** A turn ended: what the teammate changed is read again. */
  refreshTick: () => number
}

/** How long typing settles before a save goes out (ms). */
const SAVE_AFTER_MS = 600
/** How often the machine's bring-up is asked about (ms). */
const POLL_MS = 2000
/** How many answers of "nothing is being brought back" are waited out before it counts as failed. */
const IDLE_POLLS = 10

/** The file each task's panel had open: it opens on it again. */
const lastOpen = new Map<string, string>()

const isRefusal = (e: unknown, name: string) => e instanceof ApiError && e.code === name

/** One file of a task, as every cell showing it sees it. */
interface FileState {
  file: OpenTaskFile | null
  /** Text the person typed that is not saved yet. */
  held: boolean
  saving: boolean
  /** The file changed underneath and the backend could not merge: reload or keep mine. */
  conflict: boolean
  /** Both changed the same lines: pick a version for each. */
  merge: MergeConflict | null
  error: string
}

/** A task's machine and what its cells share. */
interface TaskState {
  projectId: string
  machine: TaskMachineState
  error: string
  /** What was saved or held here, for the tree's dots. */
  touched: Set<string>
  /** Cells of this task on screen. */
  users: number
  poll: ReturnType<typeof setTimeout> | null
  idlePolls: number
  /** Tree changes waiting for the machine. */
  queue: (() => Promise<unknown>)[]
}

const tasks = new Map<string, TaskState>()
const fileStates = new Map<string, FileState>()
const saveTimers = new Map<string, ReturnType<typeof setTimeout>>()
const inFlight = new Set<string>()
const again = new Set<string>()
const keyOf = (tid: string, path: string) => `${tid}\n${path}`

function taskState(tid: string): TaskState {
  let state = tasks.get(tid)
  if (!state) {
    state = reactive({
      projectId: '',
      machine: 'unknown',
      error: '',
      touched: new Set<string>(),
      users: 0,
      poll: null,
      idlePolls: 0,
      queue: [],
    }) as TaskState
    tasks.set(tid, state)
  }
  return state
}

function fileState(tid: string, path: string): FileState {
  const key = keyOf(tid, path)
  let state = fileStates.get(key)
  if (!state) {
    state = reactive({ file: null, held: false, saving: false, conflict: false, merge: null, error: '' }) as FileState
    fileStates.set(key, state)
  }
  return state
}

function scheduleSave(tid: string, path: string) {
  const key = keyOf(tid, path)
  const pending = saveTimers.get(key)
  if (pending) clearTimeout(pending)
  saveTimers.set(
    key,
    setTimeout(() => {
      saveTimers.delete(key)
      void saveHeld(tid, path)
    }, SAVE_AFTER_MS)
  )
}

/** Save the text held for this file; one save per file at a time. */
async function saveHeld(tid: string, path: string): Promise<void> {
  const task = taskState(tid)
  const key = keyOf(tid, path)
  if (!task.projectId) return
  if (inFlight.has(key)) {
    again.add(key)
    return
  }
  const kept = heldEdit(tid, path)
  if (!kept) return
  inFlight.add(key)
  const state = fileState(tid, path)
  state.saving = true
  try {
    const res = await writeFile(task.projectId, path, kept.content, null, kept.version, tid, kept.base)
    noteSaved(tid)
    const saved = res.merged && typeof res.content === 'string' ? res.content : kept.content
    const now = heldEdit(tid, path)
    if (now && now.content !== kept.content) {
      // Typed on while it went out: the rest is made against what was saved.
      holdEdit(tid, path, { content: now.content, version: res.version, base: saved })
      again.add(key)
    } else {
      releaseEdit(tid, path)
    }
    if (state.file) {
      const mine = state.file.content === kept.content
      state.file = { ...state.file, version: res.version, base: saved, content: mine ? saved : state.file.content }
      state.held = !mine
    }
    state.conflict = false
    state.merge = null
    state.error = ''
    task.machine = 'ready'
  } catch (e) {
    if (isRefusal(e, 'MachinePreparing')) {
      task.machine = 'preparing'
      watchBringUp(tid)
    } else if (e instanceof ApiError && e.status === 409) {
      const regions = (e.data as MergeConflict | undefined)?.regions
      if (regions) state.merge = e.data as MergeConflict
      else state.conflict = true
    } else {
      state.error = e instanceof Error ? e.message : t('global.saveState.failed')
    }
  } finally {
    inFlight.delete(key)
    state.saving = false
    if (again.delete(key) && task.machine === 'ready') void saveHeld(tid, path)
  }
}

/** Save every file of this task with text held, then the tree changes waiting (the machine is ready). */
async function flushHeld(tid: string) {
  for (const path of heldPaths(tid)) await saveHeld(tid, path)
  const queued = taskState(tid).queue.splice(0)
  for (const run of queued) await run()
}

/** Ask for the task's machine for the person's edits, unless that is under way. */
async function bringUp(tid: string): Promise<void> {
  const task = taskState(tid)
  if (task.machine === 'preparing') return
  task.machine = 'preparing'
  task.error = ''
  try {
    await prepareTaskMachine(tid)
    watchBringUp(tid)
  } catch (e) {
    task.machine = 'failed'
    task.error = e instanceof Error ? e.message : t('apiError.taskMachinePrepareFailed')
  }
}

function watchBringUp(tid: string) {
  const task = taskState(tid)
  if (task.poll || task.users === 0) return
  task.idlePolls = 0
  task.poll = setTimeout(() => void poll(tid), POLL_MS)
}

async function poll(tid: string) {
  const task = taskState(tid)
  task.poll = null
  if (task.users === 0) return
  try {
    const now = await getTaskMachineStatus(tid)
    if (tasks.get(tid) !== task) return
    if (now.machine === 'ready') {
      task.machine = 'ready'
      await flushHeld(tid)
      return
    }
    if (now.failed === 'unavailable' || (now.machine === null && ++task.idlePolls > IDLE_POLLS)) {
      task.machine = 'failed'
      task.error = t('apiError.taskMachinePrepareFailed')
      return
    }
  } catch {
    // A status that did not answer is asked again.
  }
  if (task.users > 0 && !task.poll) task.poll = setTimeout(() => void poll(tid), POLL_MS)
}

/** Text held from before (a reload, another visit): carry on saving it. */
async function resume(tid: string) {
  const task = taskState(tid)
  if (task.machine === 'preparing') return
  if (task.machine === 'ready') return void flushHeld(tid)
  try {
    const now = await getTaskMachineStatus(tid)
    if (now.machine === 'ready') {
      task.machine = 'ready'
      return void flushHeld(tid)
    }
    if (now.machine === 'preparing') {
      task.machine = 'preparing'
      return watchBringUp(tid)
    }
  } catch {
    // Asked for below.
  }
  await bringUp(tid)
}

/** The last cell of a task closed: nothing more is asked for it. Held text stays in storage. */
function release(tid: string) {
  const task = tasks.get(tid)
  if (!task || --task.users > 0) return
  if (task.poll) clearTimeout(task.poll)
  tasks.delete(tid)
  const prefix = `${tid}\n`
  for (const [key, timer] of saveTimers) {
    if (key.startsWith(prefix)) {
      clearTimeout(timer)
      saveTimers.delete(key)
    }
  }
  for (const key of [...fileStates.keys()]) if (key.startsWith(prefix)) fileStates.delete(key)
}

export function useTaskFiles(opts: TaskFilesOptions) {
  const files = ref<WorkspaceFile[]>([])
  const dirs = ref<string[]>([])
  const listed = ref(false)
  const listing = ref(false)
  const listError = ref('')

  const openPath = ref<string | null>(null)
  const opening = ref(false)
  const fileError = ref('')
  /** The lines a file link pointed at, to reveal in the editor. */
  const lines = ref<{ start: number; end: number } | null>(null)
  /** A tree change refused or failed. */
  const arrangeError = ref('')

  const task = () => opts.taskId()
  // This cell counts as one of its task's cells while it is mounted.
  let joined: string | null = null
  watch(
    task,
    (tid) => {
      if (joined) release(joined)
      joined = tid
      if (!tid) return
      const state = taskState(tid)
      state.users += 1
      state.projectId = opts.projectId() ?? state.projectId
    },
    { immediate: true }
  )
  const shared = computed(() => {
    const tid = task()
    return tid ? taskState(tid) : null
  })
  const current = computed(() => {
    const tid = task()
    return tid && openPath.value ? fileState(tid, openPath.value) : null
  })

  const file = computed(() => current.value?.file ?? null)
  const held = computed(() => current.value?.held ?? false)
  const saving = computed(() => current.value?.saving ?? false)
  const conflict = computed(() => current.value?.conflict ?? false)
  const merge = computed(() => current.value?.merge ?? null)
  const machine = computed<TaskMachineState>(() => shared.value?.machine ?? 'unknown')
  const saveError = computed(() => current.value?.error || shared.value?.error || '')
  const touched = computed(() => shared.value?.touched ?? new Set<string>())

  const canEdit = computed(() => !opts.readOnly() && !!file.value && !file.value.binary && !file.value.tooLarge)

  // ---------------------------------------------------------------- listing

  let listRequest = 0
  async function loadListing(opts2: { silent?: boolean } = {}) {
    const pid = opts.projectId()
    const tid = task()
    if (!pid || !tid) return
    const request = ++listRequest
    if (!opts2.silent) listing.value = true
    try {
      const page = await listFiles(pid, null, tid, 'live')
      if (request !== listRequest || task() !== tid) return
      files.value = page.data
      dirs.value = page.dirs ?? []
      listError.value = ''
      listed.value = true
    } catch (e) {
      if (request !== listRequest || opts2.silent) return
      listError.value = e instanceof Error ? e.message : t('work.room.changes.loadFailed')
    } finally {
      if (request === listRequest) listing.value = false
    }
  }

  // ---------------------------------------------------------------- one file

  let fileRequest = 0
  async function open(path: string, at: { start: number; end: number } | null = null): Promise<void> {
    const pid = opts.projectId()
    const tid = task()
    if (!pid || !tid) return
    const request = ++fileRequest
    openPath.value = path
    lines.value = at
    lastOpen.set(tid, path)
    opening.value = true
    fileError.value = ''
    const state = fileState(tid, path)
    try {
      const read = await readFile(pid, path, null, tid, 'live')
      if (request !== fileRequest || task() !== tid) return
      // A copy a machine holds takes a save: nothing has to be brought back.
      if (read.editable && read.source !== 'committed') taskState(tid).machine = 'ready'
      // Another cell is saving this file: what it shows is the latest word on it.
      if (state.saving) return
      const text = read.content
      const kept = text !== null ? heldEdit(tid, path) : null
      state.file = {
        path,
        content: kept?.content ?? text ?? '',
        base: kept?.base ?? text ?? '',
        version: kept ? kept.version : read.version,
        bytes: read.bytes,
        binary: read.binary || (text === null && !read.too_large),
        tooLarge: read.too_large,
      }
      state.held = !!kept
      state.conflict = false
      state.merge = null
      state.error = ''
      if (kept && !opts.readOnly()) void resume(tid)
    } catch (e) {
      if (request !== fileRequest || task() !== tid) return
      state.file = null
      fileError.value = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
    } finally {
      if (request === fileRequest) opening.value = false
    }
  }

  /** Opens a file by path when the task's copy has it; answers whether it does. */
  async function openFile(path: string, at: { start: number; end: number } | null = null): Promise<boolean> {
    if (!listed.value) await loadListing()
    if (!files.value.some((f) => f.path === path)) return false
    await open(path, at)
    return true
  }

  // ---------------------------------------------------------------- editing

  function edit(text: string) {
    const tid = task()
    const state = current.value
    const now = state?.file
    if (!tid || !state || !now || !canEdit.value || text === now.content) return
    state.file = { ...now, content: text }
    holdEdit(tid, now.path, { content: text, version: now.version, base: now.base })
    state.held = true
    state.error = ''
    const shared = taskState(tid)
    shared.touched = new Set(shared.touched).add(now.path)
    if (shared.machine === 'ready') scheduleSave(tid, now.path)
    // After a failed bring-up the text stays held; the person retries it.
    else if (shared.machine !== 'failed') void bringUp(tid)
  }

  /** Save at once (Ctrl+S): the text is held either way. */
  function saveNow() {
    const tid = task()
    const path = openPath.value
    if (!tid || !path || !held.value) return
    const key = keyOf(tid, path)
    const pending = saveTimers.get(key)
    if (pending) clearTimeout(pending)
    saveTimers.delete(key)
    if (machine.value === 'ready') void saveHeld(tid, path)
    else void bringUp(tid)
  }

  /** The retry offered after a bring-up or a save failed. */
  function retry() {
    const tid = task()
    if (!tid) return
    const shared = taskState(tid)
    shared.error = ''
    if (current.value) current.value.error = ''
    if (shared.machine === 'failed') {
      shared.machine = 'unknown'
      void bringUp(tid)
    } else if (openPath.value) void saveHeld(tid, openPath.value)
  }

  // ---------------------------------------------------------------- conflicts

  /** Drop the person's text and read the file as it is now. */
  function reloadLatest() {
    const tid = task()
    const path = openPath.value
    if (!tid || !path) return
    releaseEdit(tid, path)
    fileState(tid, path).held = false
    void open(path)
  }

  /** Save the person's text over whatever changed the file meanwhile. */
  async function keepMine() {
    const pid = opts.projectId()
    const tid = task()
    const state = current.value
    const now = state?.file
    if (!pid || !tid || !state || !now) return
    try {
      const latest = await readFile(pid, now.path, null, tid, 'live')
      holdEdit(tid, now.path, { content: now.content, version: latest.version, base: latest.content ?? '' })
      state.conflict = false
      await saveHeld(tid, now.path)
    } catch (e) {
      state.error = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
    }
  }

  /** The version picked for each overlapping place, saved over theirs. */
  function resolveMerge(text: string) {
    const tid = task()
    const state = current.value
    const now = state?.file
    const picked = state?.merge
    if (!tid || !state || !now || !picked) return
    state.file = { ...now, content: text, version: picked.version, base: picked.base }
    holdEdit(tid, now.path, { content: text, version: picked.version, base: picked.base })
    void saveHeld(tid, now.path)
  }
  function cancelMerge() {
    if (current.value) current.value.merge = null
  }

  // ---------------------------------------------------------------- the tree

  async function arrange(change: TaskFileArrangement): Promise<boolean> {
    const pid = opts.projectId()
    const tid = task()
    if (!pid || !tid || opts.readOnly()) return false
    arrangeError.value = ''
    const shared = taskState(tid)
    try {
      const done = await arrangeTaskFile(pid, tid, change)
      shared.machine = 'ready'
      afterArrange(tid, change, done.path)
      await loadListing({ silent: true })
      if (change.operation === 'create') await open(done.path)
      return true
    } catch (e) {
      if (isRefusal(e, 'MachinePreparing')) {
        // The backend asked for the machine; the change is made once it is ready.
        shared.queue.push(() => arrange(change))
        shared.machine = 'preparing'
        watchBringUp(tid)
        return true
      }
      arrangeError.value = e instanceof Error ? e.message : t('global.updateFailed')
      return false
    }
  }

  /** Text held for a renamed or deleted file goes with it. */
  function afterArrange(tid: string, change: TaskFileArrangement, path: string) {
    if (change.operation !== 'move' && change.operation !== 'delete') return
    const under = (p: string) => p === change.path || p.startsWith(`${change.path}/`)
    for (const p of heldPaths(tid).filter(under)) {
      const kept = heldEdit(tid, p)
      releaseEdit(tid, p)
      fileStates.delete(keyOf(tid, p))
      if (kept && change.operation === 'move') holdEdit(tid, path + p.slice(change.path.length), kept)
    }
    const now = openPath.value
    if (now && under(now)) {
      if (change.operation === 'move') void open(path + now.slice(change.path.length))
      else openPath.value = null
    }
  }

  // ---------------------------------------------------------------- lifetime

  // The panel loads the first time it is on screen, and opens the file it had.
  watch(
    () => [opts.active(), task()] as const,
    ([active, tid]) => {
      if (!active || !tid || listed.value || listing.value) return
      void loadListing().then(() => {
        const last = lastOpen.get(tid)
        if (!openPath.value && last && files.value.some((f) => f.path === last)) void open(last)
      })
      // Text held from an earlier visit is saved even if its file is not opened.
      if (heldPaths(tid).length && !opts.readOnly()) void resume(tid)
    },
    { immediate: true }
  )

  // A turn ended: the teammate may have changed files. The open file is read
  // again only when the person has nothing held in it.
  watch(
    () => opts.refreshTick(),
    () => {
      if (!listed.value) return
      void loadListing({ silent: true })
      const path = openPath.value
      if (path && !held.value && !saving.value) void open(path)
    }
  )

  function warnBeforeUnload(event: BeforeUnloadEvent) {
    if (!heldOnlyInMemory()) return
    event.preventDefault()
    event.returnValue = ''
  }
  window.addEventListener('beforeunload', warnBeforeUnload)
  onBeforeUnmount(() => {
    window.removeEventListener('beforeunload', warnBeforeUnload)
    if (joined) release(joined)
    joined = null
  })

  return {
    files,
    dirs,
    listing,
    listError,
    openPath,
    file,
    opening,
    fileError,
    lines,
    machine,
    saving,
    held,
    saveError,
    conflict,
    merge,
    touched,
    arrangeError,
    canEdit,
    loadListing,
    open,
    openFile,
    edit,
    saveNow,
    retry,
    reloadLatest,
    keepMine,
    resolveMerge,
    cancelMerge,
    arrange,
  }
}

export type TaskFilesBundle = ReturnType<typeof useTaskFiles>
