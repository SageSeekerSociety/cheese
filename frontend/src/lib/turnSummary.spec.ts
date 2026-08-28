// 本轮摘要：一轮里平台做的事折成一行。
//
// 「更新了文档」「记录了决策」「提交了验收卡」说的全是右边那栏自己会亮的事，一轮
// 四条各占一行；而这一轮到底改了哪些文件——房间里唯一没有别处可看的东西——过去
// 只在现场里躺着一行灰字。折成一行之后噪音少了三行，信息反而多了一条。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { collapseNotices } from './platformNotice'

function evt(id: string, content: string, meta: Record<string, unknown>, turn: string | null): Block {
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
    meta,
    turn_id: turn,
    created_at: '2026-08-18T09:00:00Z',
  } as unknown as Block
}

function message(id: string, turn: string | null): Block {
  return {
    id,
    project_id: 'p',
    topic_id: 't',
    kind: 'message',
    author_type: 'ai',
    author: 'cheese',
    content: '我改完了',
    reply_to: null,
    refs: [],
    turn_id: turn,
    created_at: '2026-08-18T09:00:00Z',
  } as unknown as Block
}

const CHANGES = {
  platform: true,
  changeset: {
    commit: 'abc1234',
    commits: ['abc1234'],
    files_total: 3,
    added: 120,
    removed: 8,
    files: [
      { path: 'a.ts', added: 100, removed: 4 },
      { path: 'b.ts', added: 20, removed: 4 },
    ],
    files_omitted: 1,
  },
}

describe('本轮摘要', () => {
  it('同一轮的动作行和改动摘要折成一行，带上文件数和增删', () => {
    const rows = collapseNotices([
      evt('a', '更新了文档', { action: 'doc' }, 'turn-1'),
      evt('b', '记录了决策', { action: 'decision' }, 'turn-1'),
      evt('c', '这一轮改了 3 个文件（+120 -8）', CHANGES, 'turn-1'),
    ])

    expect(rows).toHaveLength(1)
    const notice = rows[0].notice!
    expect(notice.mode).toBe('turn-summary')
    if (notice.mode !== 'turn-summary') throw new Error('unreachable')
    expect(notice.changes).toEqual({
      filesTotal: 3,
      added: 120,
      removed: 8,
      files: ['a.ts', 'b.ts'],
      filesOmitted: 1,
    })
    expect(notice.actions.map((a) => a.resource)).toEqual(['doc', 'decision'])
    expect(notice.turnId).toBe('turn-1')
  })

  it('不跨轮合并——两轮的收尾各算各的', () => {
    const rows = collapseNotices([
      evt('a', '更新了文档', { action: 'doc' }, 'turn-1'),
      evt('b', '这一轮改了 3 个文件（+120 -8）', CHANGES, 'turn-1'),
      evt('c', '更新了文档', { action: 'doc' }, 'turn-2'),
      evt('d', '这一轮改了 3 个文件（+120 -8）', CHANGES, 'turn-2'),
    ])
    expect(rows).toHaveLength(2)
    expect(rows.map((r) => (r.notice?.mode === 'turn-summary' ? r.notice.turnId : null))).toEqual(['turn-1', 'turn-2'])
  })

  it('中间隔了一条消息，折叠就断', () => {
    const rows = collapseNotices([
      evt('a', '更新了文档', { action: 'doc' }, 'turn-1'),
      message('m', 'turn-1'),
      evt('b', '这一轮改了 3 个文件（+120 -8）', CHANGES, 'turn-1'),
    ])
    expect(rows.map((r) => r.notice?.mode ?? 'message')).toEqual(['action', 'message', 'turn-summary'])
  })

  it('孤零零一条动作行不换壳——本轮摘要的理由是「改了什么」，没有就还是动作行', () => {
    const rows = collapseNotices([evt('a', '更新了文档', { action: 'doc' }, 'turn-1')])
    expect(rows[0].notice?.mode).toBe('action')
  })

  it('分不出轮次的老块不参与折叠', () => {
    const rows = collapseNotices([
      evt('a', '更新了文档', { action: 'doc' }, null),
      evt('b', '记录了决策', { action: 'decision' }, null),
    ])
    expect(rows.map((r) => r.notice?.mode)).toEqual(['action', 'action'])
  })
})
