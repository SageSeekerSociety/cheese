// What the room's socket says about the document's comments, passed to the
// document panel of the same room.
//
// The socket belongs to the chat column (useChatPanel); the comments belong to
// the document panel, several components away on another branch of the page.
// Rather than thread a counter through every layer between them, the page that
// hears a frame announces it here under the room's id, and the panel showing
// that room's document listens.
import type { DocThreadActivity } from './docThreadTypes'

export type DocCommentSignal =
  /** A thread was started, answered, resolved or reopened: read them again. */
  | { kind: 'changed' }
  /** How far the agent has got with one thread's question. */
  | ({ kind: 'activity'; thread: string } & DocThreadActivity)

type Listener = (signal: DocCommentSignal) => void
const listeners = new Map<string, Set<Listener>>()

export function announceComments(room: string, signal: DocCommentSignal): void {
  listeners.get(room)?.forEach((listener) => listener(signal))
}

/** Hear the room's comment signals until the returned function is called. */
export function listenToComments(room: string, listener: Listener): () => void {
  const set = listeners.get(room) ?? new Set()
  set.add(listener)
  listeners.set(room, set)
  return () => {
    set.delete(listener)
    if (!set.size) listeners.delete(room)
  }
}
