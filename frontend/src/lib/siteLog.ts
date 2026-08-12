// 现场 (the read-only transcript timeline) reads like a chat log, so it has to
// behave like one: newest at the bottom and visible on open, and a single
// enormous entry must not push everything else off the screen.
//
// The length rule lives here rather than in a CSS `line-clamp` alone because
// the template needs the same answer the style does — whether to render the
// 展开 affordance at all. Asking the DOM for a measured height would mean
// reading layout during render; asking the text is deterministic and testable.

/** Collapsed height for a long entry, in lines of the 现场 monospace type. */
export const SITE_CLAMP_LINES = 12

/** Beyond this, an entry is long even if it never wraps a newline. */
const SITE_CLAMP_CHARS = 900

/**
 * Whether this transcript entry should start collapsed.
 *
 * Two ways to be long, and both matter: 芝士 emits both wide prose (few
 * newlines, thousands of characters) and tall command output (many short
 * lines). Testing only one of them lets the other through.
 */
export function isLongSiteEntry(content: string): boolean {
  if (!content) return false
  if (content.length > SITE_CLAMP_CHARS) return true
  return countLines(content) > SITE_CLAMP_LINES
}

/** Lines in the entry, for the "展开全部（N 行）" label. */
export function countLines(content: string): number {
  if (!content) return 0
  return content.split('\n').length
}

/**
 * Whether an incoming entry should pull the view down with it.
 *
 * Same rule the chat pane uses: follow the tail only when the reader is already
 * parked at it. Someone who scrolled up to read something is reading it —
 * yanking them to the bottom because 芝士 emitted another tool line is worse
 * than making them scroll back down themselves.
 */
export function shouldFollowTail(
  el: { scrollTop: number; scrollHeight: number; clientHeight: number },
  threshold = 80
): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

/**
 * How many frames the tail-pin may keep re-pinning while the panel is still
 * growing. ~30 frames ≈ 500ms at 60fps, and it stops the moment the height
 * holds steady for one frame — measured, the panel settles in about 120ms.
 */
export const SITE_TAIL_PIN_FRAMES = 30

/**
 * Whether to pin the view to the bottom for another frame.
 *
 * A one-shot `scrollTop = scrollHeight` is not enough, and #327 shipped exactly
 * that. The panel renders its spinner FIRST, so at the moment the scroll runs
 * the container is one viewport tall with nothing to scroll — the assignment
 * clamps to 0 — and the real timeline then lays out underneath, leaving the
 * reader on the oldest entry, which is the bug #327 set out to fix. Measured on
 * the deployed page afterwards: scrollHeight 500 at +40ms, 2066 at +120ms,
 * scrollTop 0 the whole way.
 *
 * So re-pin while the height is still moving, and stop as soon as it isn't.
 * Both bounds matter: without the height check this would fight the reader's
 * own scrolling forever, and without the frame cap a panel that never settles
 * (a live-streaming turn) would pin for the life of the page.
 */
export function shouldKeepPinning(
  height: number,
  lastHeight: number,
  frames: number,
  maxFrames = SITE_TAIL_PIN_FRAMES
): boolean {
  return height !== lastHeight && frames < maxFrames
}
