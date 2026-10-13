// `@platform/motion`: what an app imports to make a change visible as it
// happens (references/apps.md teaches it). Like the elements, it is shell 1's
// contract with every app kept on it: it may grow, never change.
//
// `tween(source)` is a copy of a value that slides to each new value instead
// of jumping. A change that comes on the heels of another — a reader dragging
// a point or a slider — is followed at once: the hand leads, and a picture
// trailing behind it reads as lag. Nothing slides when the reader asked for
// less motion, or while the platform takes the check's still pictures.
import { getCurrentScope, isRef, onScopeDispose, type Ref, type ShallowRef, shallowRef, watch } from 'vue'

import { duration, ease, still } from '../components/motion'

/** Changes closer together than this are one continuous gesture. */
const GESTURE_MS = 120

/** The value `k` of the way from `a` to `b`: numbers in between, matched by
 *  position in an array and by key in an object; anything else is `b`. */
function between(a: unknown, b: unknown, k: number): unknown {
  if (typeof b === 'number') return typeof a === 'number' && Number.isFinite(a) ? a + (b - a) * k : b
  if (Array.isArray(b)) return b.map((v, i) => between(Array.isArray(a) ? a[i] : undefined, v, k))
  if (b && typeof b === 'object' && Object.getPrototypeOf(b) === Object.prototype) {
    const from = a && typeof a === 'object' ? (a as Record<string, unknown>) : {}
    return Object.fromEntries(Object.entries(b).map(([key, v]) => [key, between(from[key], v, k)]))
  }
  return b
}

export function tween<T>(source: Ref<T> | (() => T), options: { duration?: number } = {}): Readonly<ShallowRef<T>> {
  const read = () => (isRef(source) ? source.value : typeof source === 'function' ? source() : source) as T
  const shown = shallowRef(between(undefined, read(), 1) as T)
  let frame = 0
  let last = 0
  const stop = () => cancelAnimationFrame(frame)

  watch(
    read,
    (to) => {
      const now = performance.now()
      const gesture = now - last < GESTURE_MS
      last = now
      stop()
      if (gesture || still()) {
        shown.value = between(undefined, to, 1) as T
        return
      }
      const from = shown.value
      const ms = Math.max(0, options.duration ?? duration())
      const began = now
      const step = (t: number) => {
        const k = ms ? Math.min(1, (t - began) / ms) : 1
        shown.value = between(from, to, ease(k)) as T
        if (k < 1) frame = requestAnimationFrame(step)
      }
      frame = requestAnimationFrame(step)
    },
    { deep: true }
  )
  if (getCurrentScope()) onScopeDispose(stop)
  return shown
}
