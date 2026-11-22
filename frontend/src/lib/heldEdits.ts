// A person's edits to a task's file that are not saved yet, held in this
// browser until they are: while the task's machine is being brought back, the
// person keeps typing, and a reload, a closed tab or a failed bring-up must not
// lose a keystroke. One record per (task, path), with the version and the text
// the edits were made against, so the save that finally goes out can be merged
// with whatever changed the file meanwhile (the backend's three-way save).
//
// The browser's storage can refuse (private mode, a full quota, site data
// blocked): the record is then held in memory for this page only, and
// `heldOnlyInMemory` says so, so the page can warn before it is left.
import { storageGetJson, storageKeys, storageRemove, storageSetJson } from './browserStorage'

export interface HeldEdit {
  /** The person's text. */
  content: string
  /** The version of the file the edits were made against. */
  version: string | null
  /** The text that version names. */
  base: string
  /** When it was last changed (ms). */
  at: number
}

const PREFIX = 'cheesex.heldEdit.'
const keyOf = (task: string, path: string) => `${PREFIX}${task}:${path}`

/** Records the browser would not keep. */
const memory = new Map<string, HeldEdit>()

function valid(value: unknown): value is HeldEdit {
  const v = value as HeldEdit | null
  return (
    !!v &&
    typeof v.content === 'string' &&
    typeof v.base === 'string' &&
    (typeof v.version === 'string' || v.version === null)
  )
}

/** Hold the person's text for this file until it is saved. */
export function holdEdit(task: string, path: string, edit: Omit<HeldEdit, 'at'>): void {
  const key = keyOf(task, path)
  const record: HeldEdit = { ...edit, at: Date.now() }
  if (storageSetJson(key, record)) memory.delete(key)
  else memory.set(key, record)
}

/** The text held for this file, if any. */
export function heldEdit(task: string, path: string): HeldEdit | null {
  const key = keyOf(task, path)
  const inMemory = memory.get(key)
  if (inMemory) return inMemory
  const stored = storageGetJson<unknown>(key)
  return valid(stored) ? stored : null
}

/** The file's text was saved (or the person let it go). */
export function releaseEdit(task: string, path: string): void {
  const key = keyOf(task, path)
  memory.delete(key)
  storageRemove(key)
}

/** Every file of this task with text held. */
export function heldPaths(task: string): string[] {
  const prefix = `${PREFIX}${task}:`
  const paths = new Set<string>()
  for (const key of storageKeys(prefix)) paths.add(key.slice(prefix.length))
  for (const key of memory.keys()) if (key.startsWith(prefix)) paths.add(key.slice(prefix.length))
  return [...paths].filter((path) => heldEdit(task, path) !== null)
}

/** Some text is held where a reload would lose it. */
export function heldOnlyInMemory(): boolean {
  return memory.size > 0
}
