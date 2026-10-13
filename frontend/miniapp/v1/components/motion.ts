// How the platform's elements move: for as long as the document's own
// `--dur-base`, on its standard curve, and not at all when the reader asked for
// less motion or the platform is taking the check's still pictures.
import { token } from '../theme'

export function still(): boolean {
  return document.documentElement.dataset.mode === 'shot' || matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function duration(): number {
  const ms = parseFloat(token('dur-base'))
  return Number.isFinite(ms) ? ms : 220
}

/** The standard curve's shape, near enough for a frame-by-frame tween. */
export function ease(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}
