import { describe, expect, it } from 'vitest'

import { cardinalityEnds } from './diagramSpec'
import { feedbackArch, feedbackEr } from './feedbackDiagram'

describe('cardinalityEnds', () => {
  it('把 1-N 拆成「起点是一、终点是多」', () => {
    expect(cardinalityEnds('1-N')).toEqual({ from: '1', to: 'N' })
    expect(cardinalityEnds('N-1')).toEqual({ from: 'N', to: '1' })
  })

  it('N-N 两端都是多', () => {
    expect(cardinalityEnds('N-N')).toEqual({ from: 'N', to: 'N' })
  })

  it('M 当作 N 的同义词', () => {
    expect(cardinalityEnds('1-M')).toEqual({ from: '1', to: 'N' })
  })

  it('大小写和两侧空白都容忍', () => {
    expect(cardinalityEnds(' n - 1 ')).toEqual({ from: 'N', to: '1' })
  })

  it('没写基数时两端都留空 —— 少一个记号好过瞎标一个', () => {
    expect(cardinalityEnds(undefined)).toEqual({ from: '', to: '' })
    expect(cardinalityEnds('')).toEqual({ from: '', to: '' })
  })

  it('认不出来的写法一律留空，不猜', () => {
    expect(cardinalityEnds('一到多')).toEqual({ from: '', to: '' })
    expect(cardinalityEnds('1-N-1')).toEqual({ from: '', to: '' })
    expect(cardinalityEnds('很多')).toEqual({ from: '', to: '' })
  })
})

// 图上的每一处都得有出处，但「出处对不对」人眼看不出。这里能自动查的是另外一类错：
// 引用了一个不存在的表 / 节点 —— 那种错在图上表现为「少一根线」，不会报错。
describe('feedbackEr 的引用完整', () => {
  it('每条关系两端的表都存在', () => {
    const ids = new Set(feedbackEr.entities.map((entity) => entity.id))
    const dangling = feedbackEr.relations
      .flatMap((relation) => [relation.from, relation.to])
      .filter((id) => !ids.has(id))
    expect(dangling).toEqual([])
  })

  it('没有重复的表 id —— 重复了 React/Vue 的 key 会撞，第二张表静默不画', () => {
    const ids = feedbackEr.entities.map((entity) => entity.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('新建的表都连着至少一条关系 —— 画出来却谁也不连，读的人会以为漏画了', () => {
    const wired = new Set(feedbackEr.relations.flatMap((relation) => [relation.from, relation.to]))
    const lonely = feedbackEr.entities
      .filter((entity) => entity.isNew)
      .map((entity) => entity.id)
      .filter((id) => !wired.has(id))
    expect(lonely).toEqual([])
  })

  it('标了「待定」的表必须写清楚还没定什么，否则那个标记就是纯噪音', () => {
    const vague = feedbackEr.entities
      .filter((entity) => entity.tentative)
      .filter((entity) => !entity.note || entity.note.length < 10)
      .map((entity) => entity.id)
    expect(vague).toEqual([])
  })

  it('待定的表不许混在「既有表」里 —— 那样分组在替它下结论', () => {
    const wrong = feedbackEr.entities
      .filter((entity) => entity.tentative)
      .filter((entity) => entity.isNew !== true)
      .map((entity) => entity.id)
    expect(wrong).toEqual([])
  })

  it('自引用的两端是同一张表（评论的 parent_id 就是这么挂的）', () => {
    const loops = feedbackEr.relations.filter((relation) => relation.from === relation.to)
    expect(loops.length).toBeGreaterThan(0)
    for (const loop of loops) {
      expect(feedbackEr.entities.some((entity) => entity.id === loop.from)).toBe(true)
    }
  })
})

describe('feedbackArch 的引用完整', () => {
  const nodeIds = new Set(feedbackArch.lanes.flatMap((lane) => lane.nodes.map((node) => node.id)))

  it('每条边的两端节点都存在', () => {
    const dangling = feedbackArch.edges.flatMap((edge) => [edge.from, edge.to]).filter((id) => !nodeIds.has(id))
    expect(dangling).toEqual([])
  })

  it('没有重复的节点 id', () => {
    const ids = feedbackArch.lanes.flatMap((lane) => lane.nodes.map((node) => node.id))
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('每个节点都标了类别，否则图上是默认灰，看不出分组', () => {
    const missing = feedbackArch.lanes
      .flatMap((lane) => lane.nodes)
      .filter((node) => !node.kind)
      .map((node) => node.id)
    expect(missing).toEqual([])
  })

  it('每层不超过六个节点 —— 节点是 150px 定宽，第七个就把整层撑出容器了', () => {
    const over = feedbackArch.lanes.filter((lane) => lane.nodes.length > 6).map((lane) => lane.id)
    expect(over).toEqual([])
  })

  it('边上的文字都很短：它是画在曲线中点上的，长了会盖住旁边的线', () => {
    const long = feedbackArch.edges
      .filter((edge) => (edge.label?.length ?? 0) > 8)
      .map((edge) => `${edge.from}→${edge.to}:${edge.label}`)
    expect(long).toEqual([])
  })

  it('没有孤立节点：画在图上却谁也不连的盒子只会让人猜它是什么意思', () => {
    const wired = new Set(feedbackArch.edges.flatMap((edge) => [edge.from, edge.to]))
    const isolated = [...nodeIds].filter((id) => !wired.has(id))
    expect(isolated).toEqual([])
  })
})
