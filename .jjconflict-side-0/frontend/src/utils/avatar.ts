/**
 * Default user-avatar helpers.
 *
 * When a user has no uploaded avatar (or the avatar image fails to load) we
 * render a colored initial instead of a gray placeholder icon — matching the
 * Space avatars' visual language (see spaces/Index.vue .space-avatar-char).
 *
 * The background color is derived deterministically from the user's
 * handle/name (hash → hue) so a given user always gets the same color.
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
 * A stable, readable background color for a default avatar, derived from the
 * given seed (handle preferred, else display name). Uses HSL with a fixed
 * saturation/lightness so text stays legible on white text.
 */
export function avatarColor(seed?: string | null): string {
  const s = (seed ?? '').trim()
  if (!s) return 'hsl(0, 0%, 62%)' // neutral gray when we have nothing to hash
  const hue = hueFromString(s)
  return `hsl(${hue}, 55%, 55%)`
}
