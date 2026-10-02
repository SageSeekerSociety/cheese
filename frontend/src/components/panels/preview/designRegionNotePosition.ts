export interface NoteRect {
  left: number
  top: number
  width: number
  height: number
}
export interface RegionNoteGeometry {
  viewport: NoteRect
  region: NoteRect | null
}
export interface RegionNotePosition extends NoteRect {
  maxHeight: number
  placement: 'right' | 'left' | 'below' | 'above' | 'fallback'
  compact: boolean
}

/** Disposable overlay coordinates. The original image region is never changed. */
export function designRegionNotePosition(
  geometry: RegionNoteGeometry | null,
  card: { width: number; height: number }
): RegionNotePosition | null {
  if (!geometry) return null
  const { viewport, region } = geometry
  if (![viewport.left, viewport.top, viewport.width, viewport.height, card.width, card.height].every(Number.isFinite))
    return null
  const gap = 8
  const verticalGap = viewport.height < 48 ? 0 : gap
  const left = viewport.left + gap
  const top = viewport.top + verticalGap
  const width = Math.max(0, Math.min(300, card.width || 300, viewport.width - gap * 2))
  const availableHeight = Math.max(0, viewport.height - verticalGap * 2)
  const height = Math.min(Math.max(0, card.height), availableHeight)
  const right = viewport.left + viewport.width - gap
  const bottom = viewport.top + viewport.height - verticalGap
  if (region && [region.left, region.top, region.width, region.height].every(Number.isFinite)) {
    const candidates = [
      { placement: 'right' as const, left: region.left + region.width + gap, top: region.top },
      { placement: 'left' as const, left: region.left - width - gap, top: region.top },
      { placement: 'below' as const, left: region.left, top: region.top + region.height + gap },
      { placement: 'above' as const, left: region.left, top: region.top - height - gap },
    ]
    for (const candidate of candidates) {
      if (
        candidate.left >= left &&
        candidate.top >= top &&
        candidate.left + width <= right &&
        candidate.top + card.height <= bottom
      )
        return { ...candidate, width, height, maxHeight: availableHeight, compact: false }
    }
  }
  return {
    left,
    top: Math.max(top, bottom - height),
    width,
    height,
    maxHeight: availableHeight,
    placement: 'fallback',
    compact: availableHeight < 100,
  }
}
