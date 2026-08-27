import type { FlatRow, TopicNodeLike, TopicRelevanceLike } from './topicTree'

import { beforeEach, describe, expect, it } from 'vitest'

import {
  ancestorPathIds,
  isMyTopic,
  loadExpandedTopics,
  loadOthersGroupOpen,
  partitionByRelevance,
  saveExpandedTopics,
  saveOthersGroupOpen,
  visibleRows,
} from './topicTree'

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

// ---- 相关性分组 ----
// 「我参与的」的四种参与方式在后端合成一个布尔 i_participate（名册 / 我建的 /
// 我是验收人 / 我被 @ 过），所以前端这一侧四种情形是同一条断言；这里仍然分四条
// 写出来，是为了万一后端把某一种漏掉、看得出漏的是哪一种。
function rel(id: string, parentId: string | null, flags: Partial<TopicRelevanceLike> = {}): TopicRelevanceLike {
  return { id, parent_id: parentId, ...flags }
}
function flat(topics: TopicRelevanceLike[], depths: number[]): FlatRow<TopicRelevanceLike>[] {
  return topics.map((topic, i) => ({ topic, depth: depths[i] }))
}

describe('话题分组：与我相关 / 其他', () => {
  it('四种参与方式（名册/我建的/我是验收人/我被 @ 过）都落在上组', () => {
    // 后端把四者合成一个 i_participate=true，所以四种各来一条，结论必须一致。
    const byRoster = rel('roster', null, { i_participate: true })
    const byAuthor = rel('author', null, { i_participate: true })
    const byReviewer = rel('reviewer', null, { i_participate: true })
    const byMention = rel('mention', null, { i_participate: true })
    const { mine, others } = partitionByRelevance(flat([byRoster, byAuthor, byReviewer, byMention], [0, 0, 0, 0]))
    expect(idsOf(mine)).toEqual(['roster', 'author', 'reviewer', 'mention'])
    expect(others).toEqual([])
  })

  it('完全无关的进下组', () => {
    const { mine, others } = partitionByRelevance(
      flat([rel('mine', null, { i_participate: true }), rel('theirs', null, { i_participate: false })], [0, 0])
    )
    expect(idsOf(mine)).toEqual(['mine'])
    expect(idsOf(others)).toEqual(['theirs'])
  })

  it('awaits_me 为真但 i_participate 为假时，仍然落在上组', () => {
    // 后端保证这不该出现（awaits_me ⇒ i_participate），但"等我做事"绝不能因为
    // 上游的一个反常组合被折进下组——前端不做这个假设。
    const odd = rel('odd', null, { awaits_me: true, i_participate: false })
    expect(isMyTopic(odd)).toBe(true)
    const { mine, others } = partitionByRelevance(flat([odd], [0]))
    expect(idsOf(mine)).toEqual(['odd'])
    expect(others).toEqual([])
  })

  it('字段缺失当"相关"——没算过这两个字段的载荷不会把整个项目折起来', () => {
    const { mine, others } = partitionByRelevance(flat([rel('unknown', null)], [0]))
    expect(idsOf(mine)).toEqual(['unknown'])
    expect(others).toEqual([])
  })

  it('整棵子树跟着它的根走：子话题里有一个与我相关，整棵都上去', () => {
    const parent = rel('p', null, { i_participate: false })
    const kid = rel('p1', 'p', { i_participate: false })
    const mineKid = rel('p2', 'p', { i_participate: true })
    const { mine, others } = partitionByRelevance(flat([parent, kid, mineKid], [0, 1, 1]))
    // 父话题被带上来了——它就是"通往那个子话题的路径"；缩进因此仍有参照。
    expect(idsOf(mine)).toEqual(['p', 'p1', 'p2'])
    expect(others).toEqual([])
  })

  it('无关的子树整棵进下组，depth 原样保留（组内照旧是树）', () => {
    const rows = flat(
      [
        rel('a', null, { i_participate: true }),
        rel('t', null, { i_participate: false }),
        rel('t1', 't', { i_participate: false }),
        rel('t1x', 't1', { i_participate: false }),
      ],
      [0, 0, 1, 2]
    )
    const { mine, others } = partitionByRelevance(rows)
    expect(idsOf(mine)).toEqual(['a'])
    expect(idsOf(others)).toEqual(['t', 't1', 't1x'])
    expect(others.map((r) => r.depth)).toEqual([0, 1, 2])
  })

  it('无关但有未读：分组不看未读，未读由组头聚合成一个点', () => {
    const rows = flat([rel('t', null, { i_participate: false })], [0])
    const { others } = partitionByRelevance(rows)
    expect(idsOf(others)).toEqual(['t'])
    // 组头的那个点 = 组内未读求和，组内自己的折叠状态不影响它。
    const unread: Record<string, number> = { t: 7 }
    expect(others.reduce((sum, r) => sum + (unread[r.topic.id] ?? 0), 0)).toBe(7)
  })

  it('两组各自仍然是可折叠的树（分组不吃掉折叠）', () => {
    const rows = flat(
      [
        rel('t', null, { i_participate: false }),
        rel('t1', 't', { i_participate: false }),
        rel('t1x', 't1', { i_participate: false }),
      ],
      [0, 1, 2]
    )
    const { others } = partitionByRelevance(rows)
    const shown = visibleRows(others, { collapsed: new Set(['t']) })
    expect(idsOf(shown)).toEqual(['t'])
    expect(shown[0].hiddenCount).toBe(2)
  })

  it('孤儿兜底行自己成一组，不会跟着前一棵子树走', () => {
    const rows = flat([rel('a', null, { i_participate: true }), rel('z', 'missing', { i_participate: false })], [0, 0])
    const { mine, others } = partitionByRelevance(rows)
    expect(idsOf(mine)).toEqual(['a'])
    expect(idsOf(others)).toEqual(['z'])
  })

  it('可以换一个判定函数（判定是参数，不是写死在里面的）', () => {
    const rows = flat([rel('a', null, { i_participate: false }), rel('b', null, { i_participate: false })], [0, 0])
    const { mine } = partitionByRelevance(rows, (t) => t.id === 'b')
    expect(idsOf(mine)).toEqual(['b'])
  })
})

describe('「其他话题」组展开状态的持久化', () => {
  beforeEach(() => localStorage.clear())

  it('默认折叠：没存过就是收起来的', () => {
    expect(loadOthersGroupOpen('p1')).toBe(false)
  })

  it('按项目分开存，刷新后还在', () => {
    saveOthersGroupOpen('p1', true)
    expect(loadOthersGroupOpen('p1')).toBe(true)
    expect(loadOthersGroupOpen('p2')).toBe(false)
    expect(loadOthersGroupOpen(null)).toBe(false)
  })

  it('收回去以后不留垃圾（键不在 = 默认态）', () => {
    saveOthersGroupOpen('p1', true)
    saveOthersGroupOpen('p1', false)
    expect(localStorage.getItem('cheesex.railOthersOpen.v1:p1')).toBeNull()
  })

  it('和展开集合是两个键，互不干扰', () => {
    saveExpandedTopics('p1', new Set(['a']))
    saveOthersGroupOpen('p1', true)
    expect([...loadExpandedTopics('p1')]).toEqual(['a'])
    expect(loadOthersGroupOpen('p1')).toBe(true)
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

  it('存坏了就回到默认的收起态', () => {
    localStorage.setItem('cheesex.topicExpanded.v2:p1', '{broken')
    expect([...loadExpandedTopics('p1')]).toEqual([])
    localStorage.setItem('cheesex.topicExpanded.v2:p1', '{"not":"an array"}')
    expect([...loadExpandedTopics('p1')]).toEqual([])
  })

  it('不读旧那把键 —— 它存的是收起来的 id，含义正好相反', () => {
    // 照旧读会把用户当初收起来的那几个房间变成唯一展开的那几个，其余全藏起来。
    localStorage.setItem('cheesex.topicCollapsed.v1:p1', JSON.stringify(['a', 'b']))
    expect([...loadExpandedTopics('p1')]).toEqual([])
  })
})
