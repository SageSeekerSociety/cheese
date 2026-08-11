import type { Block } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import {
  LOAD_OLDER_THRESHOLD,
  mergeRefreshedTail,
  prependOlder,
  scrollTopAfterPrepend,
  shouldLoadOlder,
} from './blockPaging'

const b = (id: string): Block => ({ id }) as Block
const blocks = (...ids: string[]) => ids.map(b)

describe('shouldLoadOlder', () => {
  it('fires near the top when older blocks exist', () => {
    expect(shouldLoadOlder(0, { hasMore: true, loading: false })).toBe(true)
    expect(shouldLoadOlder(LOAD_OLDER_THRESHOLD, { hasMore: true, loading: false })).toBe(true)
  })

  it('stays quiet away from the top', () => {
    expect(shouldLoadOlder(LOAD_OLDER_THRESHOLD + 1, { hasMore: true, loading: false })).toBe(false)
  })

  it('does not fire when the whole history is already loaded', () => {
    expect(shouldLoadOlder(0, { hasMore: false, loading: false })).toBe(false)
  })

  it('does not stack requests while one is in flight', () => {
    // Scroll events fire per frame; without this every page would be fetched
    // a dozen times over.
    expect(shouldLoadOlder(0, { hasMore: true, loading: true })).toBe(false)
  })
})

describe('scrollTopAfterPrepend', () => {
  it('keeps the row the user is reading exactly where it was', () => {
    // 600px of older rows went in above the viewport.
    expect(scrollTopAfterPrepend({ scrollTop: 120, scrollHeight: 2000 }, 2600)).toBe(720)
  })

  it('is a no-op when nothing was inserted', () => {
    expect(scrollTopAfterPrepend({ scrollTop: 120, scrollHeight: 2000 }, 2000)).toBe(120)
  })

  it('never returns a negative offset', () => {
    expect(scrollTopAfterPrepend({ scrollTop: 10, scrollHeight: 2000 }, 1500)).toBe(0)
  })

  it('leaves the top edge pinned to the top', () => {
    expect(scrollTopAfterPrepend({ scrollTop: 0, scrollHeight: 1000 }, 1400)).toBe(400)
  })
})

describe('prependOlder', () => {
  it('puts the older page above the current window, oldest first', () => {
    const next = prependOlder({ blocks: blocks('m5', 'm6'), hasMore: true }, blocks('m3', 'm4'), true)
    expect(next.blocks.map((x) => x.id)).toEqual(['m3', 'm4', 'm5', 'm6'])
    expect(next.hasMore).toBe(true)
  })

  it('drops blocks the window already holds', () => {
    // A duplicate id would break Vue's :key and render the row twice.
    const next = prependOlder({ blocks: blocks('m4', 'm5'), hasMore: true }, blocks('m3', 'm4'), false)
    expect(next.blocks.map((x) => x.id)).toEqual(['m3', 'm4', 'm5'])
    expect(next.hasMore).toBe(false)
  })

  it('records that the top of the history has been reached', () => {
    const next = prependOlder({ blocks: blocks('m2'), hasMore: true }, blocks('m1'), false)
    expect(next.hasMore).toBe(false)
  })
})

describe('mergeRefreshedTail', () => {
  it('keeps scrollback the user already loaded', () => {
    // The poll only refetches the newest page; the older rows must survive it.
    const cached = { blocks: blocks('m1', 'm2', 'm3', 'm4'), hasMore: true }
    const fresh = { blocks: blocks('m3', 'm4', 'm5'), hasMore: true }

    const merged = mergeRefreshedTail(cached, fresh)

    expect(merged.blocks.map((x) => x.id)).toEqual(['m1', 'm2', 'm3', 'm4', 'm5'])
    // The top of the window did not move, so cached.hasMore still describes it.
    expect(merged.hasMore).toBe(true)
  })

  it('lets the fresh page win where the two overlap', () => {
    const stale = { ...b('m3'), content: 'old' } as Block
    const edited = { ...b('m3'), content: 'edited' } as Block
    const cached = { blocks: [b('m1'), stale, b('m4')], hasMore: false }
    const fresh = { blocks: [edited], hasMore: true }

    const merged = mergeRefreshedTail(cached, fresh)

    // m4 was deleted server-side and the refetch says so; m3's edit lands.
    expect(merged.blocks.map((x) => x.content)).toEqual([undefined, 'edited'])
    expect(merged.blocks.map((x) => x.id)).toEqual(['m1', 'm3'])
  })

  it('drops the stale half rather than render a timeline with a hole', () => {
    // More than a page landed while away: m3..m9 were never fetched, so
    // concatenating would show m1,m2,m10 as if they were consecutive.
    const cached = { blocks: blocks('m1', 'm2'), hasMore: false }
    const fresh = { blocks: blocks('m10', 'm11'), hasMore: true }

    const merged = mergeRefreshedTail(cached, fresh)

    expect(merged.blocks.map((x) => x.id)).toEqual(['m10', 'm11'])
    // …and the gap is now above the window, i.e. reachable by scrolling up.
    expect(merged.hasMore).toBe(true)
  })

  it('takes the fresh page when there is nothing cached', () => {
    const fresh = { blocks: blocks('m1'), hasMore: false }
    expect(mergeRefreshedTail({ blocks: [], hasMore: false }, fresh)).toEqual(fresh)
  })

  it('accepts a topic whose timeline is now empty', () => {
    const merged = mergeRefreshedTail(
      { blocks: blocks('m1'), hasMore: false },
      {
        blocks: [],
        hasMore: false,
      }
    )
    expect(merged.blocks).toEqual([])
  })
})
