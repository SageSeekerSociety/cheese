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

  it('子表都通过外键挂在主表上', () => {
    const children = feedbackEr.entities.filter((entity) => entity.group === '子表').map((e) => e.id)
    const pointed = new Set(feedbackEr.relations.map((relation) => relation.from))
    expect(children.filter((id) => !pointed.has(id))).toEqual([])
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

  it('没有孤立节点：画在图上却谁也不连的盒子只会让人猜它是什么意思', () => {
    const wired = new Set(feedbackArch.edges.flatMap((edge) => [edge.from, edge.to]))
    const isolated = [...nodeIds].filter((id) => !wired.has(id))
    expect(isolated).toEqual([])
  })
})
