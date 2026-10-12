// What the document's live connection hears about its canvases, passed to the
// canvas blocks showing them.
//
// A change to a canvas is not a change to the document's text, so it does not
// arrive through the shared document: the backend tells whoever has the
// document open (`{type: 'state', resource: 'canvas', canvas}`), the connection
// (useDocCollab) announces it here, and each block showing that canvas reads it
// again.

type Listener = () => void
const listeners = new Map<string, Set<Listener>>()

export function announceCanvas(canvas: string): void {
  listeners.get(canvas)?.forEach((listener) => listener())
}

/** Hear that the canvas changed until the returned function is called. */
export function listenToCanvas(canvas: string, listener: Listener): () => void {
  const set = listeners.get(canvas) ?? new Set()
  set.add(listener)
  listeners.set(canvas, set)
  return () => {
    set.delete(listener)
    if (!set.size) listeners.delete(canvas)
  }
}
