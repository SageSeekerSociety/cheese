/**
 * Default user-avatar helpers.
 *
 * When a user has no uploaded avatar (or the avatar image fails to load) we
 * render a colored initial instead of a gray placeholder icon — matching the
 * Space avatars' visual language (see spaces/Index.vue .space-avatar-char).
 *
 * The background color is derived deterministically from the user's
 * handle/name (hash → hue) so a given user always gets the same color.
 *
 * That background is deliberately **theme-invariant**: the same seed yields the
 * same color in light and dark mode, which is why the white `#fff` text drawn
 * on top of it is a hard-coded literal rather than a theme token. See
 * docs/design-system.md §「唯一的例外」.
 */

/** First visible character of a name, upper-cased. Falls back to '·'. */
export function avatarInitial(name?: string | null): string {
  const c = (name ?? '').trim().charAt(0)
  return c ? c.toUpperCase() : '·'
}

/**
 * Deterministic hue (0–359) from an arbitrary string.
 * Same input → same hue, so a user's color is stable across the app.
 */
function hueFromString(seed: string): number {
  let hash = 0
  for (let i = 0; i < seed.length; i++) {
    hash = (hash << 5) - hash + seed.charCodeAt(i)
    hash |= 0 // keep in 32-bit range
  }
  return Math.abs(hash) % 360
}

/**
 * The hue a seed maps to. Exported so the tests can pin it: this mapping must
 * never change, or every existing user's avatar jumps to a different color
 * family.
 */
export function avatarHue(seed?: string | null): number {
  return hueFromString((seed ?? '').trim())
}

// ---------------------------------------------------------------------------
// Background color
//
// WHY THIS IS NO LONGER `hsl(hue, 55%, 55%)` — please do not "brighten it back".
//
// HSL lightness is not perceptual: at a fixed L = 55% the real luminance of the
// color swings enormously with hue, and the legibility of the white initial on
// top swings with it. Measured across all 360 hues, the old formula produced
// white-on-background contrast ratios from **1.72:1** (hue 60, yellow) to
// **6.47:1** (hue 240, blue). 297 of the 360 hues (83%) missed WCAG AA's 4.5:1
// and 175 of them (49%) did not even clear 3:1 — the entire
// orange → yellow → green → cyan band (hues 27–201) was effectively unreadable.
// The empty-seed gray `hsl(0, 0%, 62%)` was 2.68:1. This was equally bad in
// light and dark mode; it was never a dark-theme bug.
//
// The fix keeps the hue (so nobody's avatar changes color family) and pins the
// *perceptual* lightness instead of the HSL one, via OKLCH — whose L axis is
// perceptually uniform, so every hue ends up looking equally light AND every
// hue clears AA. At L = 0.54 / C = 0.12 the contrast range over all 360 hues is
// **4.75:1 – 5.43:1** (asserted hue-by-hue in avatar.spec.ts) and the
// empty-seed gray is 5.03:1.
//
// Why we compute the color here and emit `#rrggbb` instead of just emitting an
// `oklch()` string: at C = 0.12 roughly a third of the hues fall outside the
// sRGB gamut, and how a given browser maps those back in is not something we
// can pin down or test. Fitting the chroma ourselves means the bytes the test
// checks are exactly the bytes the browser paints — and it costs nothing in
// browser support, since a hex color works everywhere.
//
// Raising L (a lighter avatar) or C (a more saturated one) pushes hues back
// under 4.5:1 — the greens around hue 155 go first. Re-run the spec before
// touching either constant.
// ---------------------------------------------------------------------------

/** Perceptual lightness (OKLCH L) shared by every avatar background. */
const AVATAR_L = 0.54
/** Target chroma; reduced per-hue wherever sRGB cannot represent it. */
const AVATAR_C = 0.12

/**
 * OKLCH → linear-light sRGB. Components may land outside [0, 1], which means
 * the color is outside the sRGB gamut.
 */
function oklchToLinearSrgb(l: number, c: number, hueDeg: number): [number, number, number] {
  const h = (hueDeg * Math.PI) / 180
  const a = c * Math.cos(h)
  const b = c * Math.sin(h)

  // OKLab → LMS' → LMS → linear sRGB (Björn Ottosson's matrices)
  const lp = l + 0.3963377774 * a + 0.2158037573 * b
  const mp = l - 0.1055613458 * a - 0.0638541728 * b
  const sp = l - 0.0894841775 * a - 1.291485548 * b
  const lc = lp * lp * lp
  const mc = mp * mp * mp
  const sc = sp * sp * sp

  return [
    4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
    -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
    -0.0041960863 * lc - 0.7034186147 * mc + 1.707614701 * sc,
  ]
}

function inSrgbGamut(rgb: [number, number, number]): boolean {
  return rgb.every((v) => v >= -1e-6 && v <= 1 + 1e-6)
}

/**
 * Largest chroma <= `c` that sRGB can actually represent at this lightness and
 * hue. Reducing chroma (rather than clipping the channels) is what keeps the
 * perceptual lightness — and therefore the contrast — intact.
 */
function fitChroma(l: number, c: number, hueDeg: number): number {
  if (inSrgbGamut(oklchToLinearSrgb(l, c, hueDeg))) return c
  let lo = 0
  let hi = c
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2
    if (inSrgbGamut(oklchToLinearSrgb(l, mid, hueDeg))) lo = mid
    else hi = mid
  }
  return lo
}

/** Linear-light channel → two-digit sRGB hex. */
function encodeChannel(u: number): string {
  const v = u <= 0.0031308 ? 12.92 * u : 1.055 * Math.pow(u, 1 / 2.4) - 0.055
  const byte = Math.round(Math.min(1, Math.max(0, v)) * 255)
  return byte.toString(16).padStart(2, '0')
}

function hexAt(chroma: number, hueDeg: number): string {
  const [r, g, b] = oklchToLinearSrgb(AVATAR_L, chroma, hueDeg)
  return `#${encodeChannel(r)}${encodeChannel(g)}${encodeChannel(b)}`
}

const hueCache = new Map<number, string>()

/** `#rrggbb` for a hue, at the fixed avatar lightness. Memoized per hue. */
function backgroundForHue(hueDeg: number): string {
  const cached = hueCache.get(hueDeg)
  if (cached !== undefined) return cached
  const hex = hexAt(fitChroma(AVATAR_L, AVATAR_C, hueDeg), hueDeg)
  hueCache.set(hueDeg, hex)
  return hex
}

/**
 * A stable, readable background color for a default avatar, derived from the
 * given seed (handle preferred, else display name). Returns `#rrggbb`.
 *
 * Every value it can return clears WCAG AA (>= 4.5:1) against the white avatar
 * text, in both themes — read the note above before changing the constants.
 */
export function avatarColor(seed?: string | null): string {
  const s = (seed ?? '').trim()
  // Nothing to hash → neutral gray at the same perceptual lightness.
  if (!s) return hexAt(0, 0)
  return backgroundForHue(hueFromString(s))
}
