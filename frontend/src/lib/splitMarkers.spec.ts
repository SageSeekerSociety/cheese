/** 父话题时间线上的「已派出」标记落在哪里 (issue #314)。
 *
 * 事故现场是这样的：一件活 8-11 09:04 被拆到子话题，父话题的时间线上什么都没
 * 变，房间里的人照着自己那份清单又做了一遍。这里钉的就是那条本该出现在 09:04
 * 的行 —— 它必须落在 09:04 之前的最后一条消息和之后的第一条消息之间，早了晚了
 * 都会指向错误的一段对话。
 */
import type { Block, Topic } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { placeSplitMarkers } from './splitMarkers'

const ROOM = 'room-1'

function block(id: string, createdAt: string, author = '张衡'): Block {
  return {
    id,
    topic_id: ROOM,
    kind: 'message',
    author_type: 'human',
    author,
    content: id,
    created_at: createdAt,
  }
}

function child(id: string, createdAt: string, extra: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: ROOM,
    title: `活 ${id}`,
    kind: 'task',
    status: 'active',
    created_at: createdAt,
    ...extra,
  }
}

/** 窗口里每条消息前面挂的标记，按渲染顺序拍平成 [锚点消息, 子话题id]。 */
function placed(p: ReturnType<typeof placeSplitMarkers>, blocks: readonly Block[]) {
  const rows: [string, string][] = []
  for (const b of blocks) {
    for (const m of p.before.get(b.id) ?? []) rows.push([b.id, m.topicId])
  }
  return rows
}

describe('已派出标记落点', () => {
  it('落在拆分那一刻的前后两条消息之间', () => {
    const blocks = [
      block('b1', '2026-08-11T09:00:00Z'),
      block('b2', '2026-08-11T09:10:00Z'),
      block('b3', '2026-08-11T09:20:00Z'),
    ]
    const topics = [child('t1', '2026-08-11T09:04:31Z')]

    const p = placeSplitMarkers(ROOM, topics, { blocks, hasMore: false })

    // 09:04 的拆分排在 09:00 之后、09:10 之前 —— 也就是挂在 b2 前面。
    expect(placed(p, blocks)).toEqual([['b2', 't1']])
    expect(p.tail).toEqual([])
  })

  it('带上标题和当前状态，点得进去', () => {
    const blocks = [block('b1', '2026-08-11T09:00:00Z'), block('b2', '2026-08-11T09:30:00Z')]
    const topics = [child('t1', '2026-08-11T09:04:31Z', { title: '进度层与记忆落地', status: 'archived' })]

    const p = placeSplitMarkers(ROOM, topics, { blocks, hasMore: false })

    expect(p.before.get('b2')).toEqual([
      {
        topicId: 't1',
        title: '进度层与记忆落地',
        status: 'archived',
        createdAt: '2026-08-11T09:04:31Z',
      },
    ])
  })

  it('刚拆出去、之后房间里还没人说话 —— 排在时间线最后', () => {
    const blocks = [block('b1', '2026-08-11T09:00:00Z')]
    const topics = [child('t1', '2026-08-11T09:04:31Z')]

    const p = placeSplitMarkers(ROOM, topics, { blocks, hasMore: false })

    expect(p.before.size).toBe(0)
    expect(p.tail.map((m) => m.topicId)).toEqual(['t1'])
  })

  it('多条拆分挂在同一条消息前面时按拆分时间排', () => {
    const blocks = [block('b1', '2026-08-11T09:00:00Z'), block('b2', '2026-08-11T12:00:00Z')]
    const topics = [child('t2', '2026-08-11T10:30:00Z'), child('t1', '2026-08-11T09:04:31Z')]

    const p = placeSplitMarkers(ROOM, topics, { blocks, hasMore: false })

    expect(placed(p, blocks)).toEqual([
      ['b2', 't1'],
      ['b2', 't2'],
    ])
  })
})

describe('时间线是个窗口，标记不能站错位置', () => {
  // 上面还有没加载的历史时，一条比窗口最老那条还早的标记，真实位置在窗口之外。
  // 硬塞在顶端会让人以为拆分就发生在这两条消息之间 —— 宁可不显示。
  it('窗口之上还有历史时，落在窗口之前的标记先不显示', () => {
    const blocks = [block('b5', '2026-08-11T14:00:00Z'), block('b6', '2026-08-11T15:00:00Z')]
    const topics = [child('t1', '2026-08-11T09:04:31Z')]

    const p = placeSplitMarkers(ROOM, topics, { blocks, hasMore: true })

    expect(p.before.size).toBe(0)
    expect(p.tail).toEqual([])
  })

  it('整段历史都在手里时，比第一条消息还早的标记显示在最前面', () => {
    const blocks = [block('b1', '2026-08-11T14:00:00Z'), block('b2', '2026-08-11T15:00:00Z')]
    const topics = [child('t1', '2026-08-11T09:04:31Z')]

    const p = placeSplitMarkers(ROOM, topics, { blocks, hasMore: false })

    expect(placed(p, blocks)).toEqual([['b1', 't1']])
  })

  it('窗口内部的标记不受未加载历史影响，照常显示', () => {
    const blocks = [block('b5', '2026-08-11T14:00:00Z'), block('b6', '2026-08-11T16:00:00Z')]
    const topics = [child('t1', '2026-08-11T15:00:00Z')]

    const p = placeSplitMarkers(ROOM, topics, { blocks, hasMore: true })

    expect(placed(p, blocks)).toEqual([['b6', 't1']])
  })

  it('一条消息都还没有：整段历史在手里就全部显示，否则先不显示', () => {
    const topics = [child('t1', '2026-08-11T09:04:31Z')]

    expect(placeSplitMarkers(ROOM, topics, { blocks: [], hasMore: false }).tail).toHaveLength(1)
    expect(placeSplitMarkers(ROOM, topics, { blocks: [], hasMore: true }).tail).toHaveLength(0)
  })
})

describe('哪些子话题该标', () => {
  const blocks = [block('b1', '2026-08-11T08:00:00Z'), block('b2', '2026-08-11T20:00:00Z')]
  const win = { blocks, hasMore: false }

  it('别的房间拆出去的活不标在这里', () => {
    const topics = [child('t1', '2026-08-11T09:00:00Z', { parent_id: 'room-2' })]

    expect(placeSplitMarkers(ROOM, topics, win).before.size).toBe(0)
  })

  // 「讨论升级 / 文档 🧩」那条路径会在源 block 上写 upgraded_to_topic_id，时间线
  // 早就把那条消息渲染成「已升级为话题，点击查看」了。再标一行就是同一件事说两遍。
  it('从某条消息升级出去的话题不重复标 —— 那条消息上已经有链接了', () => {
    const topics = [child('t1', '2026-08-11T09:00:00Z', { upgraded_from_block_id: 'b1' })]

    expect(placeSplitMarkers(ROOM, topics, win).before.size).toBe(0)
  })

  // 项目根下面挂的是房间，不是活。在根的时间线上给每个房间标一行「已派出」是噪音。
  it('房间不是派出去的活', () => {
    const topics = [child('t1', '2026-08-11T09:00:00Z', { kind: 'topic' })]

    expect(placeSplitMarkers(ROOM, topics, win).before.size).toBe(0)
  })

  it('subtopic 是 task 的历史叫法，照标', () => {
    const topics = [child('t1', '2026-08-11T09:00:00Z', { kind: 'subtopic' })]

    expect(placed(placeSplitMarkers(ROOM, topics, win), blocks)).toEqual([['b2', 't1']])
  })

  it('没选中任何话题时不算标记', () => {
    const topics = [child('t1', '2026-08-11T09:00:00Z')]

    expect(placeSplitMarkers(null, topics, win).before.size).toBe(0)
    expect(placeSplitMarkers(undefined, topics, win).tail).toEqual([])
  })
})
