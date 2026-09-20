// 「露不露面」这一格对消息同样生效。
//
// 后端在落库时给通篇没有中文的 AI 消息打上 in_room:false —— 它照常存着、照常在
// 历史里，只是不占聊天区。这里钉住前端确实认这一格：过去它只对事件生效，消息是
// 无条件显示的。

import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { collapseNotices } from './platformNotice'

function msg(id: string, content: string, meta: Record<string, unknown> | null = null): Block {
  return {
    id,
    topic_id: 't',
    kind: 'message',
    author_type: 'ai',
    author: 'cheese',
    content,
    doc_version: 1,
    refs: [],
    meta,
    created_at: '2026-09-08T00:00:00Z',
  } as unknown as Block
}

describe('聊天区里不露面的消息', () => {
  it('后端标了不露面的消息，聊天区不显示', () => {
    const rows = collapseNotices([
      msg('a', '我先看看分页是怎么实现的'),
      msg('b', 'Now the tests:', { in_room: false }),
      msg('c', '跑完了，全绿'),
    ])

    expect(rows.map((r) => r.block.id)).toEqual(['a', 'c'])
  })

  it('没有这一格的消息照常显示 —— 库里绝大多数消息的 meta 是空的', () => {
    const rows = collapseNotices([msg('a', '跑完了', null), msg('b', '再看一眼', {})])

    expect(rows.map((r) => r.block.id)).toEqual(['a', 'b'])
  })

  it('明确标了露面的消息照常显示', () => {
    const rows = collapseNotices([msg('a', '这条要露面', { in_room: true })])

    expect(rows).toHaveLength(1)
  })
})
