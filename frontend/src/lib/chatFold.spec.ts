import { describe, expect, it } from 'vitest'

import { foldHeight, overflowsFold, REPLY_FOLD_LINES } from './chatFold'

describe('the height a long reply folds to', () => {
  it('is the reading line budget times the body own line height', () => {
    expect(foldHeight(24, 20)).toBe(480)
    expect(foldHeight(24)).toBe(24 * REPLY_FOLD_LINES)
  })

  it('falls back to zero for a line height it cannot measure', () => {
    // No layout (a unit test, a hidden pane) reports NaN or 0; a clamp of 0 would
    // hide every reply, so the caller renders unfolded instead.
    for (const bad of [Number.NaN, 0, -2, Number.POSITIVE_INFINITY]) {
      expect(foldHeight(bad, 20)).toBe(0)
    }
  })
})

describe('whether a reply is long enough to fold', () => {
  it('folds only content taller than the budget', () => {
    expect(overflowsFold(500, 480)).toBe(true)
  })

  it('leaves content that exactly fills the budget unfolded', () => {
    expect(overflowsFold(480, 480)).toBe(false)
    expect(overflowsFold(481, 480)).toBe(false)
    expect(overflowsFold(482, 480)).toBe(true)
  })

  it('never folds when there is no measurable budget', () => {
    expect(overflowsFold(10_000, 0)).toBe(false)
  })
})
