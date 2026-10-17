import type { RowLight } from './roomLight'
import type { FlatRow, TopicNodeLike, VisibleRowsOptions } from './topicTree'

import { beforeEach, describe, expect, it } from 'vitest'

import { ancestorPathIds, loadExpandedTopics, saveExpandedTopics, visibleRows } from './topicTree'

import { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

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

  it('折叠后藏起来的那几盏灯也冒到父行上，不只是未读', () => {
    const lit: Record<string, RowLight> = {
      a1x: { kind: 'green', breathe: true },
      a2: { kind: 'yellow', breathe: false },
    }
    const opts: VisibleRowsOptions = { lightOf: (id) => lit[id] ?? null }

    // 展开着的时候，父行不背后代的灯——每一行自己说自己的。
    const open = visibleRows(rows, opts)
    expect(open.map((r) => r.hiddenLight)).toEqual([null, null, null, null, null])

    // 收起来之后，藏起来的灯必须还看得见，而且只画最亮的那一盏（黄压过呼吸绿）。
    const shown = visibleRows(rows, { ...opts, collapsed: new Set(['a']) })
    expect(idsOf(shown)).toEqual(['a', 'b'])
    expect(shown[0].hiddenLight).toEqual({ kind: 'yellow', breathe: false })
    // b 底下什么都没有，不能跟着一起亮。
    expect(shown[1].hiddenLight).toBeNull()
  })

  it('灯不是计数：嵌套折叠时只归最近的可见祖先', () => {
    const shown = visibleRows(rows, {
      collapsed: new Set(['a', 'a1']),
      reveal: ancestorPathIds(topics, 'a1'),
      lightOf: (id) => (id === 'a1x' ? { kind: 'green', breathe: true } : null),
    })
    expect(idsOf(shown)).toEqual(['a', 'a1', 'b'])
    // a1x 藏在 a1 底下，所以亮的是 a1；a 不该重复亮一次。
    expect(shown[1].hiddenLight).toEqual({ kind: 'green', breathe: true })
    expect(shown[0].hiddenLight).toBeNull()
  })

  it('不传灯的查询函数时一律当不亮（老调用点不受影响）', () => {
    const shown = visibleRows(rows, { collapsed: new Set(['a']) })
    expect(shown[0].hiddenLight).toBeNull()
    expect(shown[0].hiddenStalled).toBe(false)
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

describe('展开状态的持久化', () => {
  beforeEach(() => localStorage.clear())

  it('按项目分开存，刷新后还在', () => {
    saveExpandedTopics('p1', new Set(['a', 'b']))
    expect([...loadExpandedTopics('p1')].sort()).toEqual(['a', 'b'])
    expect([...loadExpandedTopics('p2')]).toEqual([])
    expect([...loadExpandedTopics(null)]).toEqual([])
  })

  it('全部收起后不留垃圾（键不在 = 默认的收起态）', () => {
    saveExpandedTopics('p1', new Set(['a']))
    saveExpandedTopics('p1', new Set())
    expect(localStorage.getItem('cheesex.topicExpanded.v2:p1')).toBeNull()
  })

  it('存坏了就回到默认的收起态，读不出来的删掉，形状不对的留着', () => {
    localStorage.setItem('cheesex.topicExpanded.v2:p1', '{broken')
    expect([...loadExpandedTopics('p1')]).toEqual([])
    // 解析不了的记录没有可留的信息，读的时候顺手清掉，免得每次都白解析一遍。
    expect(localStorage.getItem('cheesex.topicExpanded.v2:p1')).toBeNull()

    localStorage.setItem('cheesex.topicExpanded.v2:p1', '{"not":"an array"}')
    expect([...loadExpandedTopics('p1')]).toEqual([])
    // 形状不对的记录不是这个模块能判断的：留着不动，别把调用方可能认得的数据删了。
    expect(localStorage.getItem('cheesex.topicExpanded.v2:p1')).toBe('{"not":"an array"}')
  })

  it('不读旧那把键 —— 它存的是收起来的 id，含义正好相反', () => {
    // 照旧读会把用户当初收起来的那几个房间变成唯一展开的那几个，其余全藏起来。
    localStorage.setItem('cheesex.topicCollapsed.v1:p1', JSON.stringify(['a', 'b']))
    expect([...loadExpandedTopics('p1')]).toEqual([])
  })
})
