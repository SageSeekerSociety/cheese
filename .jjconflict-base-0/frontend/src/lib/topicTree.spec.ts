import type { FlatRow, TopicNodeLike } from './topicTree'

import { beforeEach, describe, expect, it } from 'vitest'

import { ancestorPathIds, loadCollapsedTopics, saveCollapsedTopics, visibleRows } from './topicTree'

// 一棵跟侧栏一样拍平的树（DFS 顺序 + depth），方便按 id 写断言：
//   a          depth 0
//   ├─ a1      depth 1
//   │   └─ a1x depth 2
//   └─ a2      depth 1
//   b          depth 0
const topics: TopicNodeLike[] = [
  { id: 'a', parent_id: null },
  { id: 'a1', parent_id: 'a' },
  { id: 'a1x', parent_id: 'a1' },
  { id: 'a2', parent_id: 'a' },
  { id: 'b', parent_id: null },
]
const rows: FlatRow[] = [
  { topic: topics[0], depth: 0 },
  { topic: topics[1], depth: 1 },
  { topic: topics[2], depth: 2 },
  { topic: topics[3], depth: 1 },
  { topic: topics[4], depth: 0 },
]
const idsOf = (list: { topic: TopicNodeLike }[]) => list.map((r) => r.topic.id)

describe('话题树折叠', () => {
  it('不折叠时每一行都在，顺序和缩进不变', () => {
    const shown = visibleRows(rows)
    expect(idsOf(shown)).toEqual(['a', 'a1', 'a1x', 'a2', 'b'])
    expect(shown.map((r) => r.depth)).toEqual([0, 1, 2, 1, 0])
  })

  it('折叠后隐藏的是全部后代，不只是直接子级', () => {
    const shown = visibleRows(rows, { collapsed: new Set(['a']) })
    expect(idsOf(shown)).toEqual(['a', 'b'])
    expect(shown[0].collapsed).toBe(true)
    expect(shown[0].hiddenCount).toBe(3)
  })

  it('只有有子话题的行才给折叠开关；叶子行折不了', () => {
    const shown = visibleRows(rows, { collapsed: new Set(['b', 'a1x']) })
    expect(idsOf(shown)).toEqual(['a', 'a1', 'a1x', 'a2', 'b'])
    expect(shown.map((r) => r.hasChildren)).toEqual([true, true, false, false, false])
    expect(shown.every((r) => !r.collapsed)).toBe(true)
  })

  it('当前选中的话题即使祖先被折叠也始终可见，通往它的整条路径都在', () => {
    const reveal = ancestorPathIds(topics, 'a1x')
    const shown = visibleRows(rows, { collapsed: new Set(['a', 'a1']), reveal })
    expect(idsOf(shown)).toEqual(['a', 'a1', 'a1x', 'b'])
    // 折叠状态如实反映用户设的值——不会因为导航被偷偷改写。
    expect(shown[0].collapsed).toBe(true)
    // 同层的兄弟仍然被收起来了，只有那条路径破例。
    expect(idsOf(shown)).not.toContain('a2')
  })

  it('没有选中话题时不破例', () => {
    const shown = visibleRows(rows, { collapsed: new Set(['a']), reveal: ancestorPathIds(topics, null) })
    expect(idsOf(shown)).toEqual(['a', 'b'])
  })

  it('折叠后未读冒到父行上，不会被吞掉', () => {
    const unread: Record<string, number> = { a: 1, a1: 2, a1x: 4, a2: 8 }
    const open = visibleRows(rows, { unreadOf: (id) => unread[id] ?? 0 })
    expect(open.map((r) => r.unreadTotal)).toEqual([1, 2, 4, 8, 0])

    const shown = visibleRows(rows, { collapsed: new Set(['a']), unreadOf: (id) => unread[id] ?? 0 })
    // 自己的 1 + 被藏起来的 2 + 4 + 8
    expect(shown[0].unreadTotal).toBe(15)
    expect(shown[0].hiddenUnread).toBe(14)
  })

  it('嵌套折叠时同一条未读只算一次', () => {
    const unread: Record<string, number> = { a1x: 4, a2: 8 }
    const reveal = ancestorPathIds(topics, 'a1')
    const shown = visibleRows(rows, {
      collapsed: new Set(['a', 'a1']),
      reveal,
      unreadOf: (id) => unread[id] ?? 0,
    })
    // a 和 a1 都是「看得见且收起来的」，a1x 只记在更近的 a1 名下。
    expect(idsOf(shown)).toEqual(['a', 'a1', 'b'])
    expect(shown[1].unreadTotal).toBe(4)
    expect(shown[0].unreadTotal).toBe(8)
  })

  it('折叠后"芝士在跑"和"等你处理"也冒到父行上，不只是未读', () => {
    const running = new Set(['a1x'])
    const awaits = new Set(['a2'])
    const opts = { runningOf: (id: string) => running.has(id), awaitsOf: (id: string) => awaits.has(id) }

    // 展开着的时候，父行不背任何人的状态——每一行自己说自己的。
    const open = visibleRows(rows, opts)
    expect(open.map((r) => r.hiddenRunning)).toEqual([false, false, false, false, false])
    expect(open.map((r) => r.hiddenAwaits)).toEqual([false, false, false, false, false])

    // 收起来之后，藏起来的动静必须还看得见——这正是原先漏掉的那个 bug。
    const shown = visibleRows(rows, { ...opts, collapsed: new Set(['a']) })
    expect(idsOf(shown)).toEqual(['a', 'b'])
    expect(shown[0].hiddenRunning).toBe(true)
    expect(shown[0].hiddenAwaits).toBe(true)
    // b 底下什么都没有，不能跟着一起亮。
    expect(shown[1].hiddenRunning).toBe(false)
    expect(shown[1].hiddenAwaits).toBe(false)
  })

  it('状态是"有没有"不是计数：嵌套折叠时只归最近的可见祖先', () => {
    const running = new Set(['a1x'])
    const shown = visibleRows(rows, {
      collapsed: new Set(['a', 'a1']),
      reveal: ancestorPathIds(topics, 'a1'),
      runningOf: (id: string) => running.has(id),
    })
    expect(idsOf(shown)).toEqual(['a', 'a1', 'b'])
    // a1x 藏在 a1 底下，所以亮的是 a1；a 不该重复亮一次。
    expect(shown[1].hiddenRunning).toBe(true)
    expect(shown[0].hiddenRunning).toBe(false)
  })

  it('不传状态查询函数时一律当没有状态（老调用点不受影响）', () => {
    const shown = visibleRows(rows, { collapsed: new Set(['a']) })
    expect(shown[0].hiddenRunning).toBe(false)
    expect(shown[0].hiddenAwaits).toBe(false)
  })

  it('中间层缺席（父话题已归档、子话题还活着）时，孙子仍归最近的可见祖先管', () => {
    // activeTree 过滤掉了 a1，但 a1x 仍带着 depth 2 留在数组里。
    const filtered = rows.filter((r) => r.topic.id !== 'a1')
    const shown = visibleRows(filtered, { collapsed: new Set(['a']) })
    expect(idsOf(shown)).toEqual(['a', 'b'])
    expect(shown[0].hiddenCount).toBe(2)
  })

  it('孤儿兜底行（环/悬挂 parent）照常出现，不会被当成谁的后代', () => {
    // tree 末尾把没被访问到的行按 depth 0 追加——它们不是前一行的后代。
    const orphan: TopicNodeLike = { id: 'z', parent_id: 'missing' }
    const withOrphan: FlatRow[] = [...rows, { topic: orphan, depth: 0 }]
    const shown = visibleRows(withOrphan, { collapsed: new Set(['a', 'b']) })
    expect(idsOf(shown)).toEqual(['a', 'b', 'z'])
  })

  it('环不会让祖先链死循环', () => {
    const cyclic: TopicNodeLike[] = [
      { id: 'x', parent_id: 'y' },
      { id: 'y', parent_id: 'x' },
    ]
    expect([...ancestorPathIds(cyclic, 'x')].sort()).toEqual(['x', 'y'])
  })
})

describe('折叠状态的持久化', () => {
  beforeEach(() => localStorage.clear())

  it('按项目分开存，刷新后还在', () => {
    saveCollapsedTopics('p1', new Set(['a', 'b']))
    expect([...loadCollapsedTopics('p1')].sort()).toEqual(['a', 'b'])
    expect([...loadCollapsedTopics('p2')]).toEqual([])
    expect([...loadCollapsedTopics(null)]).toEqual([])
  })

  it('全部展开后不留垃圾', () => {
    saveCollapsedTopics('p1', new Set(['a']))
    saveCollapsedTopics('p1', new Set())
    expect(localStorage.getItem('cheesex.topicCollapsed.v1:p1')).toBeNull()
  })

  it('存坏了就当没折叠过（宁可多显示，也不要藏掉话题）', () => {
    localStorage.setItem('cheesex.topicCollapsed.v1:p1', '{broken')
    expect([...loadCollapsedTopics('p1')]).toEqual([])
    localStorage.setItem('cheesex.topicCollapsed.v1:p1', '{"not":"an array"}')
    expect([...loadCollapsedTopics('p1')]).toEqual([])
  })
})
