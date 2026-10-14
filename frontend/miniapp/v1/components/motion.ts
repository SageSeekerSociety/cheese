// How the platform's elements move: for as long as the document's own
// `--dur-base`, on its standard curve, and not at all when the reader asked for
// less motion or the platform is taking the check's still pictures.
import { token } from '../theme'

export function still(): boolean {
  return document.documentElement.dataset.mode === 'shot' || matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** `--dur-base` in milliseconds; the token is written in seconds (`.22s`). */
export function duration(): number {
  const raw = token('dur-base').trim()
  const n = parseFloat(raw)
  if (!Number.isFinite(n)) return 220
  return raw.endsWith('ms') ? n : raw.endsWith('s') ? n * 1000 : n
}

/** The standard curve's shape, near enough for a frame-by-frame tween. */
export function ease(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}
