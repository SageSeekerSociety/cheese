import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { resyncTail } from './tailResync'

function block(id: string, minute: number, content = id, extra: Partial<Block> = {}): Block {
  return {
    id,
    kind: 'message',
    author: 'alice',
    content,
    created_at: `2026-10-07T10:${String(minute).padStart(2, '0')}:00Z`,
    ...extra,
  } as Block
}

describe('resyncTail', () => {
  it('reports only what is new or changed when the page meets the screen', () => {
    const shown = [block('a', 1), block('b', 2), block('c', 3)]
    const out = resyncTail(shown, {
      blocks: [block('b', 2), block('c', 3, 'edited'), block('d', 4)],
      hasMore: true,
    })
    expect(out.gap).toBe(false)
    expect(out.upserts.map((b) => b.id)).toEqual(['c', 'd'])
    expect(out.removed).toEqual([])
  })

  it('finds blocks retracted while the link was down', () => {
    const shown = [block('a', 1), block('b', 2), block('gone', 3)]
    const out = resyncTail(shown, { blocks: [block('b', 2), block('d', 4)], hasMore: true })
    expect(out.removed).toEqual(['gone'])
  })

  it('keeps scrollback that lies above the page', () => {
    const shown = [block('old', 0), block('a', 1)]
    const out = resyncTail(shown, { blocks: [block('a', 1), block('b', 2)], hasMore: true })
    expect(out.removed).toEqual([])
    expect(out.upserts.map((b) => b.id)).toEqual(['b'])
  })

  it('meets the screen by time even when the page starts on a block the screen does not draw', () => {
    // 最老那块是不露面的事件，屏幕上没有它；照样接得上，不算断档。
    const shown = [block('a', 1), block('b', 3)]
    const out = resyncTail(shown, { blocks: [block('hidden-event', 2), block('b', 3)], hasMore: true })
    expect(out.gap).toBe(false)
    expect(out.removed).toEqual([])
  })

  it('reports a gap when more than a page landed', () => {
    const shown = [block('a', 1)]
    const out = resyncTail(shown, { blocks: [block('x', 5), block('y', 6)], hasMore: true })
    expect(out.gap).toBe(true)
  })

  it('is not a gap when the page reaches the start of the history', () => {
    const out = resyncTail([block('a', 5)], { blocks: [block('x', 1), block('a', 5)], hasMore: false })
    expect(out.gap).toBe(false)
  })

  it('does not count a missing thread row as a change', () => {
    const thread = { id: 'th', replies: 2 }
    const shown = [block('a', 1, 'a', { thread } as Partial<Block>)]
    const out = resyncTail(shown, { blocks: [block('a', 1)], hasMore: false })
    expect(out.upserts).toEqual([])
  })
  it('is not a gap when the screen read up to the page through blocks it does not draw', () => {
    // 最新那一带整页都是不露面的事件：屏幕上最新那条可见的比这一页还旧，但读到过的
    // 不比它旧，什么都没漏。
    const shown = [block('a', 1)]
    const fresh = { blocks: [block('hidden-1', 5), block('hidden-2', 6)], hasMore: true }
    expect(resyncTail(shown, fresh, '2026-10-07T10:06:00Z').gap).toBe(false)
    expect(resyncTail(shown, fresh, '2026-10-07T10:02:00Z').gap).toBe(true)
  })
})
