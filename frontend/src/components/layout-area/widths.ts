/**
 * The width a panel keeps on its own (`LayoutPanel.width`), remembered in the browser
 * under its key: every area that places the panel opens it at the same width, and the
 * width never leaves the panel's bounds, whatever was stored.
 */
import type { LayoutPanelWidth } from './types'

import { storageGet, storageSet } from '@/lib/browserStorage'

export function clampWidth(spec: LayoutPanelWidth, width: number): number {
  return Math.min(spec.max, Math.max(spec.min, Math.round(width)))
}

/** The remembered width, or the initial one when nothing usable was stored. */
export function readWidth(spec: LayoutPanelWidth): number {
  const saved = Number(spec.storageKey ? storageGet(spec.storageKey) : null)
  return clampWidth(spec, Number.isFinite(saved) && saved > 0 ? saved : spec.initial)
}

/** Remembers a width the reader dragged to; returns it as it will be used. */
export function rememberWidth(spec: LayoutPanelWidth, width: number): number {
  const kept = clampWidth(spec, width)
  if (spec.storageKey) storageSet(spec.storageKey, String(kept))
  return kept
}
