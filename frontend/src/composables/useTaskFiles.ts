// A task's「文件」panel: the task's working copy, the one its AI teammate works
// in, browsed and edited by a person.
//
// Viewing never needs the task's environment: the listing and a file are read
// as the backend has them (off the environment, or off the forge while none
// holds the copy, then `editable: false`; a file of the task's commits says
// `source: committed`, and the panel marks it as the committed version).
// Editing does. The first edit asks for the environment in the background
// (`useTaskEnvironment`, the page's one state of it) and the person keeps
// typing; their text is held in the browser (`lib/heldEdits`) with the version
// it was made against, and saved once the environment is ready, when the files
// read off the forge are read again from it. A save that finds the file changed
// underneath is merged by the backend against that version; when it cannot be,
// the person's text stays and they choose.
//
// A cell of the 文件 panel keeps a tab for each file opened in it, remembered
// per task in this browser; closing a tab only stops showing the file — text
// held for it is saved as if the person had switched to another file. A cell
// holding one file (opened beside) keeps no tabs.
//
// A file can be open in several cells at once. What is known about one file
// of a task — its text, whether it is held, saving or in conflict — is kept
// once, here at module level, and every cell showing it reads that one copy:
// two cells on the same file show the same text, and it is saved once.
import type { WorkspaceFile } from '../cx_types'
import type { MergeConflict } from '../types/reviewComment'

import { computed, effectScope, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { ApiError, arrangeTaskFile, listFiles, readFile, writeFile } from '../api'
import { storageGetJson, storageRemove, storageSetJson } from '../lib/browserStorage'
import { noteSaved } from '../lib/handIn'
import { heldEdit, heldOnlyInMemory, heldPaths, holdEdit, releaseEdit } from '../lib/heldEdits'

import { taskEnvironment, taskEnvironmentState, useTaskEnvironment } from './useTaskEnvironment'

import { t } from '@/i18n'

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
  /** Read off the forge while no environment held the copy: read again once one does. */
  forge: boolean
  /** What is shown is the task's committed version (`source: committed`). */
  committed: boolean
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
  /** This cell keeps a tab per file opened in it (the 文件 panel, not a cell holding one file). */
  tabs?: () => boolean
}

/** How long typing settles before a save goes out (ms). */
const SAVE_AFTER_MS = 600

/** The tabs each task's panel had open, and the one shown: it opens on them again. */
interface SavedTabs {
  open: string[]
  active: string | null
}
const TABS_PREFIX = 'cheesex.taskFileTabs.'

function readTabs(tid: string): SavedTabs {
  const saved = storageGetJson<Partial<SavedTabs>>(TABS_PREFIX + tid)
  const open = Array.isArray(saved?.open) ? saved.open.filter((p): p is string => typeof p === 'string') : []
  const active = typeof saved?.active === 'string' && open.includes(saved.active) ? saved.active : null
  return { open, active }
}

function writeTabs(tid: string, tabs: SavedTabs) {
  if (tabs.open.length) storageSetJson(TABS_PREFIX + tid, tabs)
  else storageRemove(TABS_PREFIX + tid)
}

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

/** What a task's cells share. */
interface TaskState {
  projectId: string
  /** What was saved or held here, for the tree's dots. */
  touched: Set<string>
  /** Cells of this task on screen. */
  users: number
  /** Tree changes waiting for the environment. */
  queue: (() => Promise<unknown>)[]
  /** Stops following the task's environment (`followEnvironment`). */
  unfollow: () => void
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
      touched: new Set<string>(),
      users: 0,
      queue: [],
      unfollow: () => {},
    }) as TaskState
    tasks.set(tid, state)
    state.unfollow = followEnvironment(tid)
  }
  return state
}

const ready = (tid: string) => taskEnvironmentState(tid) === 'ready'

/**
 * The environment became ready: the text held for the task is saved, the tree changes waiting
 * are made, and the files read off the forge are read again from it — once per task, however
 * many of its cells are on screen.
 */
function followEnvironment(tid: string): () => void {
  // Its own scope: the cell that happened to make the task's state may close before the others.
  const scope = effectScope(true)
  scope.run(() =>
    watch(
      () => taskEnvironmentState(tid),
      (now, was) => {
        if (now === 'ready' && was !== 'ready') void flushHeld(tid).then(() => rereadForge(tid))
      }
    )
  )
  return () => scope.stop()
}

/** Files shown off the forge, with nothing typed in them: read again from the environment. */
async function rereadForge(tid: string) {
  const task = tasks.get(tid)
  if (!task?.projectId) return
  const prefix = `${tid}\n`
  for (const [key, state] of fileStates) {
    const shown = state.file
    if (!key.startsWith(prefix) || !shown?.forge || state.held || state.saving) continue
    try {
      const read = await readFile(task.projectId, shown.path, null, tid, 'live')
      if (fileStates.get(key) !== state || state.held || state.saving || read.content === null) continue
      if (!read.editable) continue
      state.file = {
        ...shown,
        content: read.content,
        base: read.content,
        version: read.version,
        bytes: read.bytes,
        forge: false,
        committed: false,
      }
    } catch {
      // What is shown stays; the next read of the file reads it from the environment.
    }
  }
}

function fileState(tid: string, path: string): FileState {
  const key = keyOf(tid, path)
  let state = fileStates.get(key)
  if (!state) {
    state = reactive({
      file: null,
      held: heldEdit(tid, path) !== null,
      saving: false,
      conflict: false,
      merge: null,
      error: '',
    }) as FileState
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
      state.file = {
        ...state.file,
        version: res.version,
        base: saved,
        content: mine ? saved : state.file.content,
        // Saved into the environment: what is shown is its copy now.
        forge: false,
        committed: false,
      }
      state.held = !mine
    } else {
      // Saved while no cell showed it (a closed tab, an earlier visit).
      state.held = heldEdit(tid, path) !== null
    }
    state.conflict = false
    state.merge = null
    state.error = ''
    taskEnvironment.sawReady(tid)
  } catch (e) {
    if (taskEnvironment.learn(tid, e)) {
      // Being prepared, or not answering: the environment's state says so, and the text is
      // saved once it is ready.
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
    if (again.delete(key) && ready(tid)) void saveHeld(tid, path)
  }
}

/** A save waiting for typing to settle goes out now (the person moved away from the file). */
function saveSoon(tid: string, path: string) {
  const key = keyOf(tid, path)
  const pending = saveTimers.get(key)
  if (!pending) return
  clearTimeout(pending)
  saveTimers.delete(key)
  if (ready(tid)) void saveHeld(tid, path)
}

/** Save every file of this task with text held, then the tree changes waiting (the environment is ready). */
async function flushHeld(tid: string) {
  for (const path of heldPaths(tid)) await saveHeld(tid, path)
  const queued = tasks.get(tid)?.queue.splice(0) ?? []
  for (const run of queued) await run()
}

/** Text held from before (a reload, another visit): carry on saving it. */
async function resume(tid: string) {
  const now = await taskEnvironment.check(tid)
  if (now === 'ready') return void flushHeld(tid)
  // After a failed bring-up the text stays held; the person retries it.
  if (now !== 'preparing' && now !== 'failed' && now !== 'unanswered') await taskEnvironment.bringUp(tid)
}

/** The last cell of a task closed: nothing more is asked for it. Held text stays in storage. */
function release(tid: string) {
  const task = tasks.get(tid)
  if (!task || --task.users > 0) return
  task.unfollow()
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
  /** The files open as tabs in this cell, in the order they sit. */
  const tabs = ref<string[]>([])
  /** The tab that was shown when the task was last left, opened once the listing says it still exists. */
  let savedActive: string | null = null
  const keepsTabs = () => opts.tabs?.() ?? true

  const task = () => opts.taskId()
  /** The task's environment, as the page knows it. */
  const environment = useTaskEnvironment(task)
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
      const saved = keepsTabs() ? readTabs(tid) : { open: [], active: null }
      tabs.value = saved.open
      savedActive = saved.active
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
  const saveError = computed(() => current.value?.error || '')
  const touched = computed(() => shared.value?.touched ?? new Set<string>())
  /** The file shown is the task's committed version: no environment held the copy when it was read. */
  const committedCopy = computed(() => !!file.value?.committed && !held.value)

  /** Tabs whose file has text not saved yet. */
  const heldTabs = computed<ReadonlySet<string>>(() => {
    const tid = task()
    return new Set(tid ? tabs.value.filter((p) => fileState(tid, p).held) : [])
  })

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
      if (request !== listRequest) return
      // The environment's own condition (not answering) is said by its note, with its retry.
      if (environment.learn(e) || opts2.silent) return
      listError.value = e instanceof Error ? e.message : ''
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
    if (keepsTabs() && !tabs.value.includes(path)) {
      // A new tab sits right after the one shown, or last when none is.
      const shown = openPath.value ? tabs.value.indexOf(openPath.value) : -1
      const slot = shown < 0 ? tabs.value.length : shown + 1
      tabs.value = [...tabs.value.slice(0, slot), path, ...tabs.value.slice(slot)]
    }
    openPath.value = path
    lines.value = at
    rememberTabs()
    opening.value = true
    fileError.value = ''
    const state = fileState(tid, path)
    try {
      const read = await readFile(pid, path, null, tid, 'live')
      if (request !== fileRequest || task() !== tid) return
      const text = read.content
      // A text file that takes no save was read off the forge: no environment holds the copy.
      const forge = !read.editable && text !== null && !read.too_large
      // A copy the environment holds takes a save: nothing has to be brought up. One read off
      // the forge though the environment was believed ready says it is gone.
      if (read.editable) environment.sawReady()
      else if (forge) environment.sawGone()
      // Another cell is saving this file: what it shows is the latest word on it.
      if (state.saving) return
      const kept = text !== null ? heldEdit(tid, path) : null
      state.file = {
        path,
        content: kept?.content ?? text ?? '',
        base: kept?.base ?? text ?? '',
        version: kept ? kept.version : read.version,
        bytes: read.bytes,
        binary: read.binary || (text === null && !read.too_large),
        tooLarge: read.too_large,
        forge,
        committed: read.source === 'committed',
      }
      state.held = !!kept
      state.conflict = false
      state.merge = null
      state.error = ''
      if (kept && !opts.readOnly()) void resume(tid)
    } catch (e) {
      if (request !== fileRequest || task() !== tid) return
      state.file = null
      // The environment's own condition is said by its note; the file is read again once it is ready.
      if (environment.learn(e)) return
      fileError.value = e instanceof Error ? e.message : ''
    } finally {
      if (request === fileRequest) opening.value = false
    }
  }

  /** Shows no file: the last tab was closed, or the file shown was deleted. */
  function showNothing() {
    fileRequest += 1
    openPath.value = null
    lines.value = null
    opening.value = false
    fileError.value = ''
  }

  function rememberTabs() {
    const tid = task()
    if (tid && keepsTabs()) writeTabs(tid, { open: tabs.value, active: openPath.value })
  }

  /**
   * Close a file's tab. Text held for it is not dropped: a save waiting for the
   * typing to settle goes out now, and one waiting for the environment goes out with
   * the others once it is ready — as when the person switches to another file.
   */
  function closeTab(path: string) {
    const tid = task()
    const at = tabs.value.indexOf(path)
    if (!tid || at < 0) return
    saveSoon(tid, path)
    const rest = tabs.value.filter((p) => p !== path)
    tabs.value = rest
    if (openPath.value === path) {
      // The neighbour on the right takes its place, or on the left at the end of the row.
      const next = rest[Math.min(at, rest.length - 1)]
      if (next) void open(next)
      else showNothing()
    }
    rememberTabs()
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
    const env = environment.state.value
    if (env === 'ready') scheduleSave(tid, now.path)
    // After a failed bring-up, or one that did not answer, the text stays held; the person retries it.
    else if (env !== 'failed' && env !== 'unanswered') void environment.bringUp()
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
    if (environment.state.value === 'ready') void saveHeld(tid, path)
    else void environment.bringUp()
  }

  /** The retry offered after a save failed. */
  function retry() {
    const tid = task()
    if (!tid) return
    if (current.value) current.value.error = ''
    if (openPath.value) void saveHeld(tid, openPath.value)
  }

  /** The retry offered after the environment failed to come up or did not answer. */
  function retryEnvironment() {
    void environment.retry()
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
      environment.sawReady()
      afterArrange(tid, change, done.path)
      await loadListing({ silent: true })
      if (change.operation === 'create') await open(done.path)
      return true
    } catch (e) {
      if (isRefusal(e, 'MachinePreparing')) {
        // The backend asked for the environment; the change is made once it is ready.
        shared.queue.push(() => arrange(change))
        environment.learn(e)
        return true
      }
      environment.learn(e)
      arrangeError.value = e instanceof Error ? e.message : t('global.updateFailed')
      return false
    }
  }

  /** Text held for a renamed or deleted file, and its tab, go with it. */
  function afterArrange(tid: string, change: TaskFileArrangement, path: string) {
    if (change.operation !== 'move' && change.operation !== 'delete') return
    const under = (p: string) => p === change.path || p.startsWith(`${change.path}/`)
    const moved = (p: string) => path + p.slice(change.path.length)
    for (const p of heldPaths(tid).filter(under)) {
      const kept = heldEdit(tid, p)
      releaseEdit(tid, p)
      fileStates.delete(keyOf(tid, p))
      if (kept && change.operation === 'move') holdEdit(tid, moved(p), kept)
    }
    const before = tabs.value
    const now = openPath.value
    if (change.operation === 'move') {
      tabs.value = before.map((p) => (under(p) ? moved(p) : p))
      if (now && under(now)) void open(moved(now))
    } else {
      tabs.value = before.filter((p) => !under(p))
      if (now && under(now)) {
        const at = before.indexOf(now)
        const next =
          before.slice(at + 1).find((p) => !under(p)) ??
          before
            .slice(0, Math.max(at, 0))
            .reverse()
            .find((p) => !under(p))
        if (next) void open(next)
        else showNothing()
      }
    }
    rememberTabs()
  }

  // ---------------------------------------------------------------- lifetime

  // The panel loads the first time it is on screen, and opens the tabs it had;
  // a file that is gone since is dropped without a word.
  watch(
    () => [opts.active(), task()] as const,
    ([active, tid]) => {
      if (!active || !tid || listed.value || listing.value) return
      void loadListing().then(() => {
        if (!listed.value || task() !== tid || !keepsTabs()) return
        const known = new Set(files.value.map((f) => f.path))
        tabs.value = tabs.value.filter((p) => known.has(p) || p === openPath.value)
        const last = savedActive && tabs.value.includes(savedActive) ? savedActive : tabs.value.at(-1)
        savedActive = null
        if (!openPath.value && last) void open(last)
        else rememberTabs()
      })
      // Text held from an earlier visit is saved even if its file is not opened.
      if (heldPaths(tid).length && !opts.readOnly()) void resume(tid)
    },
    { immediate: true }
  )

  // The environment came up after the listing was read off the forge (or failed to be read): it
  // is read again from the environment, which also holds what was never committed.
  watch(
    () => environment.state.value,
    (now, was) => {
      if (now !== 'ready' || was === 'ready' || was === 'unknown') return
      if (listed.value) void loadListing({ silent: true })
      else if (listError.value || opts.active()) void loadListing()
      if (openPath.value && !file.value && !opening.value) void open(openPath.value, lines.value)
    }
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
    listed,
    listing,
    listError,
    openPath,
    tabs,
    heldTabs,
    file,
    opening,
    fileError,
    lines,
    environment,
    committedCopy,
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
    closeTab,
    edit,
    saveNow,
    retry,
    retryEnvironment,
    reloadLatest,
    keepMine,
    resolveMerge,
    cancelMerge,
    arrange,
  }
}

export type TaskFilesBundle = ReturnType<typeof useTaskFiles>
