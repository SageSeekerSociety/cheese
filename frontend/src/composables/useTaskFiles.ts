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
import type { WorkspaceFile } from '../cx_types'
import type { MergeConflict } from '../types/reviewComment'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

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
export type TaskMachineState = 'unknown' | 'ready' | 'preparing' | 'failed' | 'no_session'

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

export function useTaskFiles(opts: TaskFilesOptions) {
  const files = ref<WorkspaceFile[]>([])
  const dirs = ref<string[]>([])
  const listed = ref(false)
  const listing = ref(false)
  const listError = ref('')

  const file = ref<OpenTaskFile | null>(null)
  const openPath = ref<string | null>(null)
  const opening = ref(false)
  const fileError = ref('')
  /** The backend said the open file takes a save as it is (a machine holds the copy). */
  const machine = ref<TaskMachineState>('unknown')

  const saving = ref(false)
  /** The open file has text the person typed that is not saved yet. */
  const held = ref(false)
  const saveError = ref('')
  /** The file changed underneath and the backend could not merge: reload or keep mine. */
  const conflict = ref(false)
  /** Both changed the same lines: pick a version for each. */
  const merge = ref<MergeConflict | null>(null)
  /** What this panel saved or holds, for the tree's dots. */
  const touched = ref<Set<string>>(new Set())
  /** A tree change refused or failed. */
  const arrangeError = ref('')

  const task = () => opts.taskId()

  const canEdit = computed(
    () =>
      !opts.readOnly() && machine.value !== 'no_session' && !!file.value && !file.value.binary && !file.value.tooLarge
  )

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
  async function open(path: string): Promise<void> {
    const pid = opts.projectId()
    const tid = task()
    if (!pid || !tid) return
    const request = ++fileRequest
    openPath.value = path
    lastOpen.set(tid, path)
    opening.value = true
    fileError.value = ''
    conflict.value = false
    merge.value = null
    saveError.value = ''
    try {
      const read = await readFile(pid, path, null, tid, 'live')
      if (request !== fileRequest || task() !== tid) return
      const text = read.content
      file.value = {
        path,
        content: text ?? '',
        base: text ?? '',
        version: read.version,
        bytes: read.bytes,
        binary: read.binary || (text === null && !read.too_large),
        tooLarge: read.too_large,
      }
      // A copy a machine holds takes a save: nothing has to be brought back.
      if (read.editable && read.source !== 'committed') machine.value = 'ready'
      const kept = heldEdit(tid, path)
      held.value = false
      if (kept && text !== null) {
        file.value = { ...file.value, content: kept.content, base: kept.base, version: kept.version }
        held.value = true
        void resume()
      }
    } catch (e) {
      if (request !== fileRequest || task() !== tid) return
      file.value = null
      fileError.value = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
    } finally {
      if (request === fileRequest) opening.value = false
    }
  }

  /** Opens a file by path when the task's copy has it; answers whether it does. */
  async function openFile(path: string): Promise<boolean> {
    if (!listed.value) await loadListing()
    if (!files.value.some((f) => f.path === path)) return false
    await open(path)
    return true
  }

  // ---------------------------------------------------------------- editing

  let saveTimer: ReturnType<typeof setTimeout> | null = null
  function edit(text: string) {
    const tid = task()
    const now = file.value
    if (!tid || !now || !canEdit.value || text === now.content) return
    file.value = { ...now, content: text }
    holdEdit(tid, now.path, { content: text, version: now.version, base: now.base })
    held.value = true
    touched.value = new Set(touched.value).add(now.path)
    saveError.value = ''
    if (machine.value === 'ready') {
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => {
        saveTimer = null
        void saveHeld(now.path)
      }, SAVE_AFTER_MS)
    } else if (machine.value !== 'failed') {
      // After a failed bring-up the text stays held; the person retries it.
      void bringUp()
    }
  }

  /** Save at once (Ctrl+S): the text is held either way. */
  function saveNow() {
    const path = openPath.value
    if (!path || !held.value) return
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = null
    if (machine.value === 'ready') void saveHeld(path)
    else void bringUp()
  }

  const inFlight = new Set<string>()
  const again = new Set<string>()
  async function saveHeld(path: string): Promise<void> {
    const pid = opts.projectId()
    const tid = task()
    if (!pid || !tid) return
    if (inFlight.has(path)) {
      again.add(path)
      return
    }
    const kept = heldEdit(tid, path)
    if (!kept) return
    inFlight.add(path)
    const isOpen = () => openPath.value === path && task() === tid
    if (isOpen()) saving.value = true
    try {
      const res = await writeFile(pid, path, kept.content, null, kept.version, tid, kept.base)
      noteSaved(tid)
      const saved = res.merged && typeof res.content === 'string' ? res.content : kept.content
      const now = heldEdit(tid, path)
      if (now && now.content !== kept.content) {
        // Typed on while it went out: the rest is made against what was saved.
        holdEdit(tid, path, { content: now.content, version: res.version, base: saved })
        again.add(path)
      } else {
        releaseEdit(tid, path)
      }
      if (isOpen() && file.value) {
        const mine = file.value.content === kept.content
        file.value = {
          ...file.value,
          version: res.version,
          base: saved,
          content: mine ? saved : file.value.content,
        }
        held.value = !mine
        conflict.value = false
        merge.value = null
        saveError.value = ''
      }
      machine.value = 'ready'
    } catch (e) {
      if (isRefusal(e, 'MachinePreparing')) {
        machine.value = 'preparing'
        watchBringUp()
      } else if (isRefusal(e, 'NoTaskSession')) {
        machine.value = 'no_session'
      } else if (e instanceof ApiError && e.status === 409) {
        const regions = (e.data as MergeConflict | undefined)?.regions
        if (isOpen()) {
          if (regions) merge.value = e.data as MergeConflict
          else conflict.value = true
        }
      } else if (isOpen()) {
        saveError.value = e instanceof Error ? e.message : t('global.saveState.failed')
      }
    } finally {
      inFlight.delete(path)
      if (isOpen()) saving.value = false
      if (again.delete(path) && machine.value === 'ready') void saveHeld(path)
    }
  }

  /** Save every file of this task that has text held (the machine just became ready). */
  async function flushHeld() {
    const tid = task()
    if (!tid) return
    for (const path of heldPaths(tid)) await saveHeld(path)
    const queued = arrangeQueue.splice(0)
    for (const change of queued) await arrange(change)
  }

  // ---------------------------------------------------------------- the machine

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let idlePolls = 0
  let disposed = false

  /** Ask for the task's machine for the person's edits, unless that is under way. */
  async function bringUp(): Promise<void> {
    const tid = task()
    if (!tid || machine.value === 'preparing' || machine.value === 'no_session') return
    machine.value = 'preparing'
    saveError.value = ''
    try {
      await prepareTaskMachine(tid)
      watchBringUp()
    } catch (e) {
      if (isRefusal(e, 'NoTaskSession')) machine.value = 'no_session'
      else {
        machine.value = 'failed'
        saveError.value = e instanceof Error ? e.message : t('apiError.taskMachinePrepareFailed')
      }
    }
  }

  function watchBringUp() {
    if (pollTimer || disposed) return
    idlePolls = 0
    pollTimer = setTimeout(poll, POLL_MS)
  }

  async function poll() {
    pollTimer = null
    const tid = task()
    if (!tid || disposed) return
    try {
      const now = await getTaskMachineStatus(tid)
      if (disposed || task() !== tid) return
      if (now.machine === 'ready') {
        machine.value = 'ready'
        await flushHeld()
        return
      }
      if (now.failed === 'no_session') {
        machine.value = 'no_session'
        return
      }
      if (now.failed === 'unavailable' || (now.machine === null && ++idlePolls > IDLE_POLLS)) {
        machine.value = 'failed'
        saveError.value = t('apiError.taskMachinePrepareFailed')
        return
      }
    } catch {
      // A status that did not answer is asked again.
    }
    pollTimer = setTimeout(poll, POLL_MS)
  }

  /** Text held from before (a reload, another visit): carry on saving it. */
  async function resume() {
    const tid = task()
    if (!tid || machine.value === 'preparing' || opts.readOnly()) return
    if (machine.value === 'ready') return void flushHeld()
    try {
      const now = await getTaskMachineStatus(tid)
      if (now.machine === 'ready') {
        machine.value = 'ready'
        return void flushHeld()
      }
      if (now.machine === 'preparing') {
        machine.value = 'preparing'
        return watchBringUp()
      }
      if (now.failed === 'no_session') {
        machine.value = 'no_session'
        return
      }
    } catch {
      // Asked for below.
    }
    await bringUp()
  }

  /** The retry offered after a bring-up or a save failed. */
  function retry() {
    saveError.value = ''
    if (machine.value === 'failed') {
      machine.value = 'unknown'
      void bringUp()
    } else if (openPath.value) void saveHeld(openPath.value)
  }

  // ---------------------------------------------------------------- conflicts

  /** Drop the person's text and read the file as it is now. */
  function reloadLatest() {
    const tid = task()
    const path = openPath.value
    if (!tid || !path) return
    releaseEdit(tid, path)
    held.value = false
    void open(path)
  }

  /** Save the person's text over whatever changed the file meanwhile. */
  async function keepMine() {
    const pid = opts.projectId()
    const tid = task()
    const now = file.value
    if (!pid || !tid || !now) return
    try {
      const latest = await readFile(pid, now.path, null, tid, 'live')
      holdEdit(tid, now.path, { content: now.content, version: latest.version, base: latest.content ?? '' })
      conflict.value = false
      await saveHeld(now.path)
    } catch (e) {
      saveError.value = e instanceof Error ? e.message : t('work.room.changes.readFileFailed')
    }
  }

  /** The version picked for each overlapping place, saved over theirs. */
  function resolveMerge(text: string) {
    const tid = task()
    const now = file.value
    const picked = merge.value
    if (!tid || !now || !picked) return
    file.value = { ...now, content: text, version: picked.version, base: picked.base }
    holdEdit(tid, now.path, { content: text, version: picked.version, base: picked.base })
    void saveHeld(now.path)
  }
  function cancelMerge() {
    merge.value = null
  }

  // ---------------------------------------------------------------- the tree

  const arrangeQueue: TaskFileArrangement[] = []
  async function arrange(change: TaskFileArrangement): Promise<boolean> {
    const pid = opts.projectId()
    const tid = task()
    if (!pid || !tid || opts.readOnly()) return false
    arrangeError.value = ''
    try {
      const done = await arrangeTaskFile(pid, tid, change)
      machine.value = 'ready'
      afterArrange(tid, change, done.path)
      await loadListing({ silent: true })
      if (change.operation === 'create') await open(done.path)
      return true
    } catch (e) {
      if (isRefusal(e, 'MachinePreparing')) {
        // The backend asked for the machine; the change is made once it is ready.
        arrangeQueue.push(change)
        machine.value = 'preparing'
        watchBringUp()
        return true
      }
      if (isRefusal(e, 'NoTaskSession')) machine.value = 'no_session'
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
      if (kept && change.operation === 'move') holdEdit(tid, path + p.slice(change.path.length), kept)
    }
    const now = openPath.value
    if (now && under(now)) {
      if (change.operation === 'move') void open(path + now.slice(change.path.length))
      else {
        openPath.value = null
        file.value = null
        held.value = false
      }
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
      if (heldPaths(tid).length && !opts.readOnly()) void resume()
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
    disposed = true
    window.removeEventListener('beforeunload', warnBeforeUnload)
    if (pollTimer) clearTimeout(pollTimer)
    // Typing that had not settled yet is held already; it is saved next time.
    if (saveTimer) clearTimeout(saveTimer)
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
