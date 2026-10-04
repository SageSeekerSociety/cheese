// 表情回应的乐观更新：本地先翻一份、服务端那份覆盖、失败回滚，以及「回声先到时
// 不回滚把它盖掉」。
import type { ReactionAgg } from '../cx_types'

import { describe, expect, it, vi } from 'vitest'

import { reactOptimistically, toggleReaction } from './reactions'

const cell = (emoji: string, authors: string[]): ReactionAgg => ({ emoji, count: authors.length, authors })
const authorsOf = (list: ReactionAgg[], emoji: string) => list.find((x) => x.emoji === emoji)?.authors ?? []
const emojisOf = (list: ReactionAgg[]) => list.map((x) => x.emoji)

describe('toggleReaction（本地翻一份聚合）', () => {
  it('这条上还没表过态：加一格', () => {
    expect(toggleReaction([], '👍', 'alice')).toEqual([cell('👍', ['alice'])])
  })

  it('加到已有的那一格上：人数加一，别人还在', () => {
    const out = toggleReaction([cell('👍', ['bob'])], '👍', 'alice')
    expect(authorsOf(out, '👍')).toEqual(['bob', 'alice'])
    expect(out.find((x) => x.emoji === '👍')?.count).toBe(2)
  })

  it('取消：去掉我那一个；人走光了整格消失', () => {
    expect(toggleReaction([cell('👍', ['alice'])], '👍', 'alice')).toEqual([])
    expect(emojisOf(toggleReaction([cell('👍', ['alice', 'bob'])], '👍', 'alice'))).toEqual(['👍'])
  })

  it('没有身份（me 为空）时不假装表了态', () => {
    const before = [cell('👍', ['bob'])]
    expect(toggleReaction(before, '🎉', null)).toBe(before)
  })

  it('不改动传进来的那一份', () => {
    const before = [cell('👍', ['bob'])]
    toggleReaction(before, '👍', 'alice')
    expect(before[0].authors).toEqual(['bob'])
    expect(before[0].count).toBe(1)
  })
})

describe('reactOptimistically（先画、后覆盖、失败回滚）', () => {
  it('成功：先本地翻出一份画上去，再用服务端那份覆盖', async () => {
    let shown: ReactionAgg[] | undefined
    const apply = (_id: string, list: ReactionAgg[]) => (shown = list)
    const server = [cell('👍', ['alice', 'bob'])]
    const fail = vi.fn()
    await reactOptimistically('blk', '👍', 'alice', {
      before: () => shown,
      apply,
      send: async () => ({ reactions: server }),
      fail,
    })
    expect(shown).toEqual(server)
    expect(fail).not.toHaveBeenCalled()
  })

  it('先把乐观的那一份画上去——不用等服务端', () => {
    let shown: ReactionAgg[] | undefined
    let release!: () => void
    const gate = new Promise<void>((res) => (release = res))
    void reactOptimistically('blk', '👍', 'alice', {
      before: () => shown,
      apply: (_id, list) => (shown = list),
      send: async () => {
        await gate
        return { reactions: [] }
      },
      fail: () => {},
    })
    // 请求还在路上，界面已经是「我表过态」
    expect(authorsOf(shown!, '👍')).toEqual(['alice'])
    release()
  })

  it('失败：回到发请求前那一份，并报错', async () => {
    const before = [cell('👍', ['bob'])]
    let shown: ReactionAgg[] = before
    const apply = (_id: string, list: ReactionAgg[]) => (shown = list)
    const fail = vi.fn()
    const p = reactOptimistically('blk', '👍', 'alice', {
      before: () => shown,
      apply,
      send: async () => {
        throw new Error('boom')
      },
      fail,
    })
    // 画上去的是乐观的（alice 也表了态），还没回滚
    expect(authorsOf(shown, '👍')).toEqual(['bob', 'alice'])
    await p
    expect(shown).toBe(before)
    expect(fail).toHaveBeenCalledOnce()
  })

  it('回声先到：期间有更新的聚合落进来，失败时不再回滚把它盖掉', async () => {
    const before = [cell('👍', ['bob'])]
    let shown: ReactionAgg[] = before
    const apply = (_id: string, list: ReactionAgg[]) => (shown = list)
    const fail = vi.fn()
    let release!: () => void
    const gate = new Promise<void>((res) => (release = res))
    const p = reactOptimistically('blk', '👍', 'alice', {
      before: () => shown,
      apply,
      send: async () => {
        await gate
        throw new Error('slow fail')
      },
      fail,
    })
    // 服务端的回声（整份聚合）在请求还没回来时先落进来
    const echo = [cell('👍', ['bob', 'carol'])]
    apply('blk', echo)
    release()
    await p
    // 失败没有把回声抹掉——它带的是服务端的事实
    expect(shown).toBe(echo)
    expect(fail).toHaveBeenCalledOnce()
  })
})
