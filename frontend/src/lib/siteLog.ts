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
