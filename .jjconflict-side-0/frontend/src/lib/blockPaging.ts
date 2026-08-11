// Cursor paging for the chat timeline.
//
// The panel used to render every block a topic ever had — 2226 rows / 2.1 MB on
// a real topic, which is what wedged the browser. Now it holds a WINDOW: the
// newest page, extended upwards as the user scrolls back.
//
// The tricky parts are all pure functions, so they can actually be tested (the
// repo has no @vue/test-utils, so component-level tests don't run):
//   - where to put scrollTop after prepending older rows (the "scroll jump"),
//   - when a scroll position means "fetch the previous page",
//   - how a background cache refresh merges into a window the user paged back.
import type { Block } from '../cx_types'

// One page. Big enough that a normal topic never pages at all, small enough
// that the 2226-block topic opens on ~50 rows instead of all of them.
export const PAGE_SIZE = 50

// How close to the top (px) starts fetching the previous page. A whole viewport
// of slack, so the rows are usually there before the user reaches them.
export const LOAD_OLDER_THRESHOLD = 400

// A slice of a topic's timeline, oldest-first, plus whether older blocks exist
// above it. `hasMore` is about OLDER blocks only — the window always ends at
// the newest block, so "more" can only lie above.
export interface BlockWindow {
  blocks: Block[]
  hasMore: boolean
}

export interface ScrollState {
  scrollTop: number
  scrollHeight: number
}

/** Whether this scroll position should trigger a fetch of the previous page. */
export function shouldLoadOlder(scrollTop: number, state: { hasMore: boolean; loading: boolean }): boolean {
  if (!state.hasMore || state.loading) return false
  return scrollTop <= LOAD_OLDER_THRESHOLD
}

/**
 * Where scrollTop must land after older rows are prepended, so the content the
 * user is looking at does not move.
 *
 * Prepending grows the scrollable content ABOVE the viewport; the browser keeps
 * scrollTop as-is, which silently scrolls the view upwards by exactly the
 * height that was inserted. Adding that delta back pins the reader in place —
 * without it, every page load yanks the timeline and re-triggers the loader.
 */
export function scrollTopAfterPrepend(before: ScrollState, afterScrollHeight: number): number {
  const grew = afterScrollHeight - before.scrollHeight
  // Never scroll to a negative offset if the content somehow shrank.
  return Math.max(0, before.scrollTop + grew)
}

/**
 * Extend a window upwards with a freshly fetched older page.
 *
 * De-dupes on id: a block can arrive twice if the tail shifted between the two
 * requests, and a duplicated key would break Vue's list rendering outright.
 */
export function prependOlder(current: BlockWindow, older: Block[], hasMore: boolean): BlockWindow {
  const known = new Set(current.blocks.map((b) => b.id))
  return {
    blocks: [...older.filter((b) => !known.has(b.id)), ...current.blocks],
    hasMore,
  }
}

/**
 * Merge a freshly fetched newest page into a window the user may have already
 * paged back through.
 *
 * The background unread poll refetches only the newest page. Overwriting the
 * cache with it would throw away scrollback the user had loaded — come back to
 * the topic and the history you had scrolled through is gone.
 *
 * The two slices are stitched at the fresh page's oldest block: everything the
 * cache holds ABOVE that block is kept, and the fresh page replaces the tail
 * (so edits, deletions and new arrivals in the tail all take effect).
 *
 * If they do NOT overlap, more than a page landed while we were away and the
 * blocks between the two slices were never fetched. Concatenating would render
 * a timeline with a silent hole in it, so the stale half is dropped and the
 * fresh page stands alone — the gap is then reachable by scrolling up.
 */
export function mergeRefreshedTail(cached: BlockWindow, fresh: BlockWindow): BlockWindow {
  if (!cached.blocks.length || !fresh.blocks.length) return fresh
  const seam = cached.blocks.findIndex((b) => b.id === fresh.blocks[0].id)
  if (seam < 0) return fresh
  return {
    blocks: [...cached.blocks.slice(0, seam), ...fresh.blocks],
    // The top of the window did not move, so what lies above it did not change.
    hasMore: cached.hasMore,
  }
}
