import { expect, it } from 'vitest'

import { designRegionNotePosition } from './designRegionNotePosition'

const viewport = { left: 0, top: 0, width: 800, height: 600 }
const card = { width: 300, height: 100 }
it.each([
  { region: { left: 80, top: 70, width: 100, height: 70 }, placement: 'right', left: 188, top: 70 },
  { region: { left: 650, top: 70, width: 100, height: 70 }, placement: 'left', left: 342, top: 70 },
  { region: { left: 80, top: 540, width: 100, height: 40 }, placement: 'above', left: 80, top: 432 },
])('keeps a fully fitting card next to the region at $placement', ({ region, placement, left, top }) => {
  expect(designRegionNotePosition({ viewport, region }, card)).toMatchObject({
    placement,
    left,
    top,
    width: 300,
    height: 100,
  })
})
it('keeps a draft inside a narrow viewport when no side fits', () => {
  expect(
    designRegionNotePosition(
      { viewport: { ...viewport, width: 240 }, region: { left: 20, top: 80, width: 100, height: 70 } },
      card
    )
  ).toMatchObject({ placement: 'fallback', left: 8, top: 492, width: 224, maxHeight: 584 })
})
it('uses the current viewport for an offscreen region rather than an old anchor', () => {
  expect(
    designRegionNotePosition({ viewport: { left: 30, top: 40, width: 240, height: 200 }, region: null }, card)
  ).toMatchObject({ placement: 'fallback', left: 38, top: 132, width: 224, maxHeight: 184 })
})
it('offers a compact draft when the visible pane is too short for editing', () => {
  expect(designRegionNotePosition({ viewport: { ...viewport, height: 80 }, region: null }, card)).toMatchObject({
    placement: 'fallback',
    top: 8,
    height: 64,
    maxHeight: 64,
    compact: true,
  })
})
it('does not position against missing or nonfinite layout measurements', () => {
  expect(designRegionNotePosition(null, card)).toBeNull()
  expect(designRegionNotePosition({ viewport: { ...viewport, top: NaN }, region: null }, card)).toBeNull()
})
