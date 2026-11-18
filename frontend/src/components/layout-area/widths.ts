/**
 * The width a panel keeps on its own (`LayoutPanel.width`), remembered in the browser
 * under its key: every area that places the panel opens it at the same width, and the
 * width never leaves the panel's bounds, whatever was stored.
 *
 * One live value per key, shared by everyone who reads it: a width dragged in one place
 * (the area's divider, or a drawer showing the same panel outside the area) is the width
 * every other place shows at once.
 */
import type { Ref } from 'vue'
import type { LayoutPanelWidth } from './types'

import { ref } from 'vue'

import { storageGet, storageSet } from '@/lib/browserStorage'

export function clampWidth(spec: LayoutPanelWidth, width: number): number {
  return Math.min(spec.max, Math.max(spec.min, Math.round(width)))
}

/** The remembered width, or the initial one when nothing usable was stored. */
export function readWidth(spec: LayoutPanelWidth): number {
  const saved = Number(spec.storageKey ? storageGet(spec.storageKey) : null)
  return clampWidth(spec, Number.isFinite(saved) && saved > 0 ? saved : spec.initial)
}

const live = new Map<string, Ref<number>>()

/** The width as it is now, shared by every reader of the same key. */
export function rememberedWidth(spec: LayoutPanelWidth): Readonly<Ref<number>> {
  if (!spec.storageKey) return ref(readWidth(spec))
  let width = live.get(spec.storageKey)
  if (!width) {
    width = ref(readWidth(spec))
    live.set(spec.storageKey, width)
  }
  return width
}

/**
 * Sets the width. `persist` off changes only the live value, for every step of a drag;
 * the width the drag ends on is then remembered once.
 */
export function rememberWidth(spec: LayoutPanelWidth, width: number, persist = true): number {
  const kept = clampWidth(spec, width)
  const current = rememberedWidth(spec) as Ref<number>
  current.value = kept
  if (persist && spec.storageKey) storageSet(spec.storageKey, String(kept))
  return kept
}
