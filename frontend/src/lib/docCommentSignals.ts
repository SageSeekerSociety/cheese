// What the document's live connection hears about its comments, passed to
// whatever shows the document's threads.
//
// The connection belongs to the editor (useDocCollab); the threads belong to
// their own composable (useDocThreads). Rather than thread a counter between
// them, the connection announces each frame here under the document's id, and
// whoever shows that document's threads listens.
import type { DocThreadActivity } from './docThreadTypes'

export type DocCommentSignal =
  /** A thread was started, answered, resolved or reopened: read them again. */
  | { kind: 'changed' }
  /** How far the agent has got with one thread's question. */
  | ({ kind: 'activity'; thread: string } & DocThreadActivity)

type Listener = (signal: DocCommentSignal) => void
const listeners = new Map<string, Set<Listener>>()

export function announceComments(document: string, signal: DocCommentSignal): void {
  listeners.get(document)?.forEach((listener) => listener(signal))
}

/** Hear the document's comment signals until the returned function is called. */
export function listenToComments(document: string, listener: Listener): () => void {
  const set = listeners.get(document) ?? new Set()
  set.add(listener)
  listeners.set(document, set)
  return () => {
    set.delete(listener)
    if (!set.size) listeners.delete(document)
  }
}
