/** 表情回应的乐观更新：点下去记号立刻动、失败退回原样，而服务器的全量聚合（以及
 *  并发的 `reaction` 帧）盖上去不会把同一格数两遍。 */
import type { ReactionAgg } from '../cx_types'

import { describe, expect, it, vi } from 'vitest'

import { runReactionToggle, toggleReactionAgg } from './optimisticReactions'

describe('toggleReactionAgg (和服务器算同一件事)', () => {
  it('新表情：我一个人的一组，追加在末尾', () => {
    expect(toggleReactionAgg(undefined, '👀', 'alice')).toEqual([{ emoji: '👀', count: 1, authors: ['alice'] }])
  })

  it('已经有的表情：我加到 authors 末尾（服务器按回应时间排）', () => {
    const before: ReactionAgg[] = [{ emoji: '👍', count: 1, authors: ['bob'] }]
    expect(toggleReactionAgg(before, '👍', 'alice')).toEqual([{ emoji: '👍', count: 2, authors: ['bob', 'alice'] }])
  })

  it('我已经回应过：撤掉我', () => {
    const before: ReactionAgg[] = [{ emoji: '👍', count: 2, authors: ['bob', 'alice'] }]
    expect(toggleReactionAgg(before, '👍', 'alice')).toEqual([{ emoji: '👍', count: 1, authors: ['bob'] }])
  })

  it('这一组只剩我：整组消失（没有 count 0 的格子）', () => {
    const before: ReactionAgg[] = [{ emoji: '🎉', count: 1, authors: ['alice'] }]
    expect(toggleReactionAgg(before, '🎉', 'alice')).toEqual([])
  })

  it('别的组原样不动', () => {
    const before: ReactionAgg[] = [{ emoji: '👍', count: 1, authors: ['bob'] }]
    expect(toggleReactionAgg(before, '👀', 'alice')).toEqual([
      { emoji: '👍', count: 1, authors: ['bob'] },
      { emoji: '👀', count: 1, authors: ['alice'] },
    ])
  })

  it('来回点一下就回到原样（自己就是自己的逆）', () => {
    const before: ReactionAgg[] = [{ emoji: '👍', count: 1, authors: ['bob'] }]
    const there = toggleReactionAgg(before, '👍', 'alice')
    expect(toggleReactionAgg(there, '👍', 'alice')).toEqual(before)
  })
})

describe('runReactionToggle（乐观）', () => {
  it('请求还没回来，记号已经在眼前；回来后用服务器的聚合收口', async () => {
    let shown: ReactionAgg[] = []
    const server: ReactionAgg[] = [{ emoji: '👀', count: 1, authors: ['alice'] }]
    let resolve: (r: ReactionAgg[]) => void = () => {}
    const toggle = vi.fn(() => new Promise<ReactionAgg[]>((res) => (resolve = res)))
    const fail = vi.fn()

    const pending = runReactionToggle('👀', 'alice', {
      current: () => shown,
      apply: (next) => (shown = next),
      toggle,
      fail,
    })
    expect(shown).toEqual([{ emoji: '👀', count: 1, authors: ['alice'] }])

    resolve(server)
    await pending
    expect(shown).toEqual(server)
    expect(fail).not.toHaveBeenCalled()
  })

  it('并发的 WS 帧带同一份聚合：盖两次也不会数两遍', async () => {
    let shown: ReactionAgg[] = []
    const server: ReactionAgg[] = [{ emoji: '👀', count: 1, authors: ['alice'] }]
    const pending = runReactionToggle('👀', 'alice', {
      current: () => shown,
      apply: (next) => (shown = next),
      toggle: async () => server,
      fail: () => {},
    })
    // 帧先于 HTTP 响应到达：它带的是同一份全量聚合。
    shown = server
    await pending
    // HTTP 响应再盖同一份 —— count 还是 1，不是 2。
    expect(shown).toEqual(server)
    expect(shown[0]?.count).toBe(1)
  })

  it('请求失败：退回点击前的样子，并收尾报错', async () => {
    const before: ReactionAgg[] = [{ emoji: '👍', count: 1, authors: ['bob'] }]
    let shown: ReactionAgg[] = before
    const boom = new Error('nope')
    const fail = vi.fn()

    await runReactionToggle('👍', 'alice', {
      current: () => shown,
      apply: (next) => (shown = next),
      toggle: async () => {
        throw boom
      },
      fail,
    })
    expect(shown).toEqual(before)
    expect(fail).toHaveBeenCalledWith(boom)
  })
})
