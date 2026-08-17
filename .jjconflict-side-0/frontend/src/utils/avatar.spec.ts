import { describe, expect, it } from 'vitest'

import { avatarColor, avatarHue, avatarInitial } from './avatar'

/**
 * The avatar background is theme-invariant and always carries white `#fff`
 * text, so its only legibility requirement is contrast against white. These
 * tests measure that on the string `avatarColor()` actually returns — the same
 * bytes the browser paints — using the WCAG 2.x relative-luminance formula,
 * which is deliberately a completely different computation from the OKLCH
 * forward math in avatar.ts.
 */

const AA_CONTRAST = 4.5

function parseHex(hex: string): [number, number, number] {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/.exec(hex)
  if (!m) throw new Error(`not a #rrggbb color: ${hex}`)
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)]
}

/** WCAG 2.x relative luminance of an `#rrggbb` color. */
function relativeLuminance(hex: string): number {
  const [r, g, b] = parseHex(hex).map((byte) => {
    const c = byte / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio of white (#fff, luminance 1) against `hex`. */
function contrastWithWhite(hex: string): number {
  return 1.05 / (relativeLuminance(hex) + 0.05)
}

/**
 * One seed per hue, covering all 360 of them, so the assertions below run
 * through the real public API rather than an internal shortcut.
 */
const seedPerHue: string[] = (() => {
  const found = new Map<number, string>()
  for (let i = 0; found.size < 360 && i < 200_000; i++) {
    const seed = `seed-${i}`
    const hue = avatarHue(seed)
    if (!found.has(hue)) found.set(hue, seed)
  }
  return Array.from({ length: 360 }, (_, hue) => {
    const seed = found.get(hue)
    if (seed === undefined) throw new Error(`no seed found for hue ${hue}`)
    return seed
  })
})()

describe('avatarColor', () => {
  it('covers every hue in the test corpus', () => {
    expect(new Set(seedPerHue.map((s) => avatarHue(s))).size).toBe(360)
  })

  it('returns #rrggbb', () => {
    for (const seed of seedPerHue) {
      expect(avatarColor(seed)).toMatch(/^#[0-9a-f]{6}$/)
    }
  })

  it('clears WCAG AA against white text on all 360 hues', () => {
    const failures: string[] = []
    for (let hue = 0; hue < 360; hue++) {
      const color = avatarColor(seedPerHue[hue])
      const ratio = contrastWithWhite(color)
      if (ratio < AA_CONTRAST) failures.push(`hue ${hue} → ${color} = ${ratio.toFixed(2)}:1`)
    }
    expect(failures).toEqual([])
  })

  it('clears WCAG AA for the empty-seed fallback', () => {
    for (const seed of ['', '   ', null, undefined]) {
      const color = avatarColor(seed)
      expect(color).toMatch(/^#[0-9a-f]{6}$/)
      expect(contrastWithWhite(color)).toBeGreaterThanOrEqual(AA_CONTRAST)
    }
  })

  it('keeps perceived lightness even across hues', () => {
    // Equal WCAG luminance is what makes no avatar look obviously brighter
    // than its neighbour. Pinning the spread also pins the failure mode we are
    // fixing: the old hsl(hue, 55%, 55%) spanned 1.72:1 – 6.47:1, a 3.8x range.
    const ratios = seedPerHue.map((seed) => contrastWithWhite(avatarColor(seed)))
    const min = Math.min(...ratios)
    const max = Math.max(...ratios)
    expect(min).toBeGreaterThanOrEqual(4.5)
    expect(max / min).toBeLessThan(1.2)
  })

  it('is deterministic', () => {
    for (const seed of ['andylizf', 'pengwenbo', '芝士', 'x']) {
      expect(avatarColor(seed)).toBe(avatarColor(seed))
    }
    expect(avatarColor('  andylizf  ')).toBe(avatarColor('andylizf'))
  })

  it('gives different hues different colors', () => {
    expect(new Set(seedPerHue.map((s) => avatarColor(s))).size).toBeGreaterThan(300)
  })
})

describe('avatarHue', () => {
  // The hue assignment must survive any change to the background formula —
  // changing it would move every existing user to a different color family.
  it('is unchanged for known handles', () => {
    expect(avatarHue('andylizf')).toBe(5)
    expect(avatarHue('n1ctheboy')).toBe(165)
    expect(avatarHue('andy')).toBe(218)
    expect(avatarHue('ligan')).toBe(343)
    expect(avatarHue('pengwenbo')).toBe(119)
    expect(avatarHue('芝士')).toBe(190)
  })

  it('is stable in [0, 360)', () => {
    for (const seed of seedPerHue) {
      const hue = avatarHue(seed)
      expect(Number.isInteger(hue)).toBe(true)
      expect(hue).toBeGreaterThanOrEqual(0)
      expect(hue).toBeLessThan(360)
    }
  })
})

describe('avatarInitial', () => {
  it('upper-cases the first visible character', () => {
    expect(avatarInitial('andy')).toBe('A')
    expect(avatarInitial('  彭文博 ')).toBe('彭')
  })

  it('falls back to the middot', () => {
    expect(avatarInitial('')).toBe('·')
    expect(avatarInitial(null)).toBe('·')
    expect(avatarInitial(undefined)).toBe('·')
  })
})
