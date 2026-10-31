// 对话里只画后端那张表说要给人看的平台事件，AI 队友交出来的东西放在那次工作的最后。
import type { Block, ShownAs } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { runEdgeBetween } from './chatGrouping'
import { collapseNotices } from './platformNotice'

let at = 0
function block(over: Partial<Block> & { shown_as?: ShownAs }): Block {
  at += 1
  return {
    id: `b${at}`,
    conversation_id: 'room',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: `line ${at}`,
    created_at: `2026-10-31T08:${String(at).padStart(2, '0')}:00Z`,
    meta: {},
    ...over,
  } as Block
}

describe('给不给人看', () => {
  it('后端说不给人看的平台事件，对话里不画', () => {
    const preparing = block({ shown_as: 'hidden', meta: { event_type: 'machine_provisioning' } })
    const failed = block({ shown_as: 'for_agent', meta: { event_type: 'turn_failed' } })
    expect(collapseNotices([preparing, failed]).map((row) => row.block.id)).toEqual([failed.id])
  })
})

describe('交出来的东西放在那次工作的最后', () => {
  it('工作途中新建的文档，画在那次工作说的最后一句话后面', () => {
    const first = block({ kind: 'message', author: 'cheese', author_type: 'participant', turn_id: 't1' })
    const doc = block({ shown_as: 'card', turn_id: 't1', meta: { action: 'doc', doc_created: true } })
    const last = block({ kind: 'message', author: 'cheese', author_type: 'participant', turn_id: 't1' })
    const later = block({ kind: 'message', author: 'lin', author_type: 'participant', turn_id: null })
    expect(collapseNotices([first, doc, last, later]).map((row) => row.block.id)).toEqual([
      first.id,
      last.id,
      doc.id,
      later.id,
    ])
  })

  it('不挪出那次工作：别的话照原来的先后', () => {
    const doc = block({ shown_as: 'card', turn_id: 't1', meta: { action: 'doc', doc_created: true } })
    const other = block({ kind: 'message', author: 'lin', author_type: 'participant', turn_id: null })
    expect(collapseNotices([doc, other]).map((row) => row.block.id)).toEqual([doc.id, other.id])
  })
})

describe('这次工作的结果接在它说的话后面', () => {
  it('平台替它写下的改动摘要，不另起头像和名字', () => {
    const said = block({ kind: 'message', author: 'cheese', author_type: 'participant', turn_id: 't1' })
    const changes = block({ shown_as: 'result', turn_id: 't1', meta: { changeset: { files_total: 1 } } })
    expect(runEdgeBetween(said, changes, { broken: false })).toBe('cont')
  })

  it('平台替它说的话不是它说的，另起一行', () => {
    const said = block({ kind: 'message', author: 'cheese', author_type: 'participant', turn_id: 't1' })
    const failed = block({ shown_as: 'for_agent', turn_id: 't1', meta: { event_type: 'turn_failed' } })
    expect(runEdgeBetween(said, failed, { broken: false })).toBe('start')
  })
})
