/**
 * What a panel keeps on its own, whatever arrangement it is placed in: the width of its
 * cell (`LayoutPanel.width`) and whether that cell is folded (`LayoutPanel.foldKey`),
 * remembered in the browser under their keys. The width never leaves the panel's bounds,
 * whatever was stored.
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

const folds = new Map<string, Ref<boolean>>()

/** Whether a panel's cell is folded to a strip (`LayoutPanel.foldKey`), shared by every reader. */
export function rememberedFold(key: string): Readonly<Ref<boolean>> {
  let folded = folds.get(key)
  if (!folded) {
    folded = ref(storageGet(key) === '1')
    folds.set(key, folded)
  }
  return folded
}

export function rememberFold(key: string, folded: boolean): void {
  const current = rememberedFold(key) as Ref<boolean>
  if (current.value === folded) return
  current.value = folded
  storageSet(key, folded ? '1' : '0')
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
