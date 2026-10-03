// Cursor paging for the chat timeline.
//
// The panel used to render every block a topic ever had — 2226 rows / 2.1 MB on
// a real topic, which is what wedged the browser. Now it holds a WINDOW: the
// newest page, extended upwards as the user scrolls back — or, opened at an old
// message, a stretch from the middle that grows both ways until it meets the
// newest page (see components/room/composables/useTimeline).
//
// The tricky parts are pure functions, so they can be tested on their own:
//   - where to put scrollTop after prepending older rows (the "scroll jump"),
//   - when a scroll position means "fetch the previous / next page",
//   - how a background cache refresh merges into a window the user paged back,
//   - when a middle stretch has met the newest page.
import type { Block, ReactionAgg } from '../cx_types'

// One page. Big enough that a normal topic never pages at all, small enough
// that the 2226-block topic opens on ~50 rows instead of all of them.
export const PAGE_SIZE = 50

// How close to the top (px) starts fetching the previous page. A whole viewport
// of slack, so the rows are usually there before the user reaches them.
export const LOAD_OLDER_THRESHOLD = 400

// How many blocks one topic's window may hold. Paging back has no natural end —
// the reader can keep going up through a topic that is years long — so without
// a ceiling the window climbs back to the 2226 rows / 2.1 MB that wedged the
// browser, just more slowly. 600 is a dozen pages: far more scrollback than is
// on screen at once, far less than the whole timeline.
export const MAX_WINDOW = 600

// A slice of a topic's timeline, oldest-first, plus whether older blocks exist
// above it. `hasMore` is about OLDER blocks only; whether a middle stretch has
// newer blocks below it is the timeline's `hasNewer`, not part of a window.
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
 * Whether this scroll position should fetch the next newer page: a window opened
 * in the middle of the history grows downwards the way it grows upwards.
 * `fromBottom` is how far the viewport's bottom edge is from the content's.
 */
export function shouldLoadNewer(fromBottom: number, state: { hasNewer: boolean; loading: boolean }): boolean {
  if (!state.hasNewer || state.loading) return false
  return fromBottom <= LOAD_OLDER_THRESHOLD
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
 * Where a block that arrives on its own goes in a window — a live frame, or the
 * broker replaying a running turn to a socket that just connected, which sends
 * every frame of that turn again, most of them older than the newest page.
 *
 * In time order. One older than the window's first block, while history above
 * it is still unloaded, is not placed at all: it belongs to that history, and
 * the page holding it brings it when the reader scrolls up. Appended at the
 * bottom instead, it sits out of order, and the older pages then arrive already
 * known, so prependOlder adds nothing and paging back never gets past them.
 */
export function placeBlock(window: BlockWindow, block: Block): Block[] | null {
  const { blocks } = window
  const at = Date.parse(block.created_at)
  const last = blocks.at(-1)
  if (!last || at >= Date.parse(last.created_at)) return [...blocks, block]
  if (window.hasMore && at < Date.parse(blocks[0].created_at)) return null
  const next = blocks.findIndex((b) => Date.parse(b.created_at) > at)
  return [...blocks.slice(0, next), block, ...blocks.slice(next)]
}

/**
 * A freshly read page with the live frames that arrived while it was in flight
 * applied on top: edits and retractions win over the snapshot, and a block the
 * snapshot lacks is placed by placeBlock — never simply appended.
 */
export function applyLiveChanges(
  window: BlockWindow,
  changes: Map<string, Block | null>,
  reactions: Map<string, ReactionAgg[]>
): Block[] {
  const withReactions = (block: Block) =>
    reactions.has(block.id) ? { ...block, reactions: reactions.get(block.id)! } : block
  const blocks = new Map(window.blocks.map((block) => [block.id, block]))
  const unplaced: Block[] = []
  for (const [id, block] of changes) {
    if (!block) blocks.delete(id)
    else if (blocks.has(id)) blocks.set(id, block)
    else unplaced.push(block)
  }
  let placed: BlockWindow = { ...window, blocks: [...blocks.values()].map(withReactions) }
  for (const block of unplaced)
    placed = { ...placed, blocks: placeBlock(placed, withReactions(block)) ?? placed.blocks }
  return placed.blocks
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
 * Split a window that has grown past the cap: the oldest `max` blocks stay, the
 * newest overflow comes back so the caller can hold it aside. `null` when the
 * window already fits.
 *
 * The overflow is taken from the NEWEST end on purpose. Paging older means the
 * reader is scrolling UP, so the rows that must not move are the ones above the
 * viewport they are reading; dropping them would delete what they just pulled
 * in. The rows dropped here sit BELOW the viewport, where removing them moves
 * nothing on screen.
 *
 * It is a separate step from `prependOlder` (and never folded into it) because
 * the scroll compensation around a prepend measures the change in `scrollHeight`
 * — see `scrollTopAfterPrepend`. A trim in the same DOM update would subtract
 * the height it removed and pull the reader up by that much on every page. Do
 * the prepend, compensate, then trim.
 */
export function capWindow(blocks: Block[], max = MAX_WINDOW): { keep: Block[]; dropped: Block[] } | null {
  if (blocks.length <= max) return null
  return { keep: blocks.slice(0, max), dropped: blocks.slice(max) }
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

/**
 * A window opened in the middle of the history (`around` a message), and the
 * newest window kept live beside it: if the two meet, one continuous window
 * that ends at the newest block; otherwise null.
 *
 * They meet when the middle window already holds the newest window's first
 * block, or the newest window holds the middle one's last block. Either way
 * the newest window's copy wins from the seam on, since live frames (edits,
 * retractions, arrivals) have been landing there, not in the middle window.
 * A middle window that reached the newest block on its own (`reachedNewest`)
 * meets any newest window: nothing can lie between them.
 */
export function joinNewest(middle: BlockWindow, newest: BlockWindow, reachedNewest = false): BlockWindow | null {
  if (!newest.blocks.length) return reachedNewest ? middle : null
  const seam = middle.blocks.findIndex((b) => b.id === newest.blocks[0].id)
  if (seam >= 0) return { blocks: [...middle.blocks.slice(0, seam), ...newest.blocks], hasMore: middle.hasMore }
  const last = middle.blocks.at(-1)
  const overlap = last ? newest.blocks.findIndex((b) => b.id === last.id) : -1
  if (overlap >= 0)
    return { blocks: [...middle.blocks.slice(0, -1), ...newest.blocks.slice(overlap)], hasMore: middle.hasMore }
  if (!reachedNewest) return null
  // The middle window reached the newest block when it was fetched; anything the
  // live window holds that it lacks arrived after that.
  const known = new Set(middle.blocks.map((b) => b.id))
  return { blocks: [...middle.blocks, ...newest.blocks.filter((b) => !known.has(b.id))], hasMore: middle.hasMore }
}
