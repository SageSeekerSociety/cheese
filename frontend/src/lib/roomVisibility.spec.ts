// 这条事件在对话里露不露面。
//
// 过去这是从 `author_type` 读出来的：system 露面，ai 不露面。于是「露不露面」和
// 「谁写的」共用一格，写错了没有任何报错——事件安静地永远不出现。现在它自己有一
// 格 `meta.in_room`，这里守住的就是那一格说了算、`author_type` 说了不算。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { collapseNotices } from './platformNotice'

function evt(id: string, content: string, over: Partial<Block> & { meta?: Record<string, unknown> } = {}): Block {
  return {
    id,
    project_id: 'p',
    topic_id: 't',
    kind: 'event',
    author_type: 'system',
    author: 'cheese',
    content,
    reply_to: null,
    refs: [],
    meta: {},
    turn_id: null,
    created_at: '2026-08-18T09:00:00Z',
    ...over,
  } as unknown as Block
}

function shown(blocks: Block[]): string[] {
  return collapseNotices(blocks).map((row) => row.block.content)
}

describe('房间露面', () => {
  it('标了 in_room: false 的事件不进时间线', () => {
    expect(shown([evt('a', '读了 app/x.py', { meta: { in_room: false } }), evt('b', '这一轮改了 1 个文件')])).toEqual([
      '这一轮改了 1 个文件',
    ])
  })

  it('没标的事件都露面——芝士写的也一样', () => {
    expect(shown([evt('a', '分身查完了：查分页接口现状', { author_type: 'ai' })])).toEqual([
      '分身查完了：查分页接口现状',
    ])
  })

  it('作者是谁不决定露不露面：芝士写的能进，平台写的也能被藏', () => {
    expect(
      shown([
        evt('a', '平台自己收拾干净了', { meta: { in_room: false } }),
        evt('b', '芝士说的一句话', { author_type: 'ai', meta: { in_room: true } }),
      ])
    ).toEqual(['芝士说的一句话'])
  })

  it('藏起来的事件不参与折叠——它不在时间线上，折不进也折不掉', () => {
    // 两条同内容的淡行中间夹一条 现场-only 的，仍然折成一行；被藏的那条既不
    // 出现，也不打断折叠。
    expect(
      shown([
        evt('a', '编辑了文档'),
        evt('b', '读了 app/x.py', { author_type: 'ai', meta: { in_room: false } }),
        evt('c', '编辑了文档'),
      ])
    ).toEqual(['编辑了文档'])
  })
})
