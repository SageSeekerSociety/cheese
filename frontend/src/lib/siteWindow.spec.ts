// 「现场」那一窗的缓存：打开时画出来的是最后一页，而往上翻的游标必须和画出来的
// 那一页对得上——对不上就会漏掉中间几页，或者干脆翻不动。
import type { Block } from '../cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { cachedWindow, forgetWindows, rememberWindow } from './siteWindow'

function row(id: string, second: number, cursor: string | null): Block {
  return {
    id,
    conversation_id: 'room',
    kind: 'event',
    author_type: 'participant',
    author: 'cheese-a',
    content: '',
    created_at: `2026-10-19T10:00:${String(second).padStart(2, '0')}Z`,
    refs: [],
    meta: { record: { type: 'assistant' }, parts: [], ...(cursor ? { cursor } : {}) },
  } as Block
}

beforeEach(forgetWindows)

describe('the window held for next time', () => {
  it('paints the newest page and pages up from its own oldest row', () => {
    const blocks = Array.from({ length: 5 }, (_, i) => row(`r${i}`, i, `c${i}`))
    rememberWindow('room', null, { blocks, hasMore: false, oldestCursor: 'c0', newestCursor: 'c4' }, 2)

    const held = cachedWindow('room', null, 2)!

    expect(held.blocks.map((b) => b.id)).toEqual(['r3', 'r4'])
    // 画的是 r3 开头，往上翻就得从 r3 自己接着读——不是从整窗最老的 c0。
    expect(held.oldestCursor).toBe('c3')
    expect(held.hasMore, '上面还有没画出来的').toBe(true)
  })

  it('pages up from the oldest painted row that carries a cursor', () => {
    // socket 换上来的平台记账行不带游标。画出来的第一行正好是它时，不能因此把
    // 「上面还有」连同游标一起丢掉（那会让往上翻整场失效）。退到这一页里最早
    // 那条**带着游标**的：它已经在屏上，往回读到的东西会重叠几条，由 reader
    // 拼之前按 id 去重挡掉，不会漏也不会重。
    const blocks = [row('r0', 0, 'c0'), row('r1', 1, null), row('r2', 2, 'c2')]
    rememberWindow('room', null, { blocks, hasMore: false, oldestCursor: 'c0', newestCursor: 'c2' }, 2)

    const held = cachedWindow('room', null, 2)!

    expect(held.blocks.map((b) => b.id)).toEqual(['r1', 'r2'])
    expect(held.hasMore).toBe(true)
    // 退到 r2 自己的游标：往回读会带上已经画出来的 r2，reader 去重后正好不重不漏。
    expect(held.oldestCursor).toBe('c2')
  })

  it('paints nothing when no row in the page can be located', () => {
    // 一条带游标的都没有：这批行没法接着往上读，宁可不画（退回整页读）。
    const blocks = [row('r0', 0, null), row('r1', 1, null)]
    rememberWindow('room', null, { blocks, hasMore: true, oldestCursor: null, newestCursor: null }, 1)

    expect(cachedWindow('room', null, 1)).toBeNull()
  })
})
