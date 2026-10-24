// 哪几张卡是一组：同一条通知（`meta.notice_id`）的几道题挨着排，就是一组，只有组头
// 占一行；单独一道题不进表，照单题的卡片画。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { askGroupKey, askHeader, askMultiSelect, groupAskBlocks } from './askGroups'

function ask(id: string, notice: string | null, extra: Record<string, unknown> = {}): Block {
  return {
    id,
    content: `问题 ${id}`,
    kind: 'message',
    author: 'cheese',
    author_type: 'participant',
    conversation_id: 'c',
    project_id: 'p',
    seq: 1,
    refs: [],
    reactions: [],
    created_at: '2026-10-22T00:00:00Z',
    meta: {
      options: [{ text: 'A' }, { text: 'B' }],
      ...(notice ? { notice_id: notice } : {}),
      ...extra,
    },
  } as unknown as Block
}

function plain(id: string): Block {
  return {
    id,
    content: '一句话',
    kind: 'message',
    author: 'alice',
    author_type: 'participant',
    conversation_id: 'c',
    project_id: 'p',
    seq: 1,
    refs: [],
    reactions: [],
    created_at: '2026-10-22T00:00:00Z',
    meta: {},
  } as unknown as Block
}

describe('askGroups', () => {
  it('同一条通知的几道题并成一组，只有头一条露脸', () => {
    const { heads, members, hidden } = groupAskBlocks([ask('a', 'g'), ask('b', 'g'), ask('c', 'g')])

    expect([...heads]).toEqual(['a'])
    expect(members.get('a')?.map((b) => b.id)).toEqual(['a', 'b', 'c'])
    expect([...hidden]).toEqual(['b', 'c'])
  })

  it('单题不分组：照旧自己占一行', () => {
    const { heads, hidden } = groupAskBlocks([ask('a', 'g')])

    expect(heads.size).toBe(0)
    expect(hidden.size).toBe(0)
  })

  it('中间插了别的消息就断成两组，不替它们连线', () => {
    const { members, hidden } = groupAskBlocks([
      ask('a', 'g1'),
      ask('b', 'g1'),
      plain('x'),
      ask('c', 'g2'),
      ask('d', 'g2'),
    ])

    expect(members.get('a')?.map((b) => b.id)).toEqual(['a', 'b'])
    expect(members.get('c')?.map((b) => b.id)).toEqual(['c', 'd'])
    expect(hidden.has('x')).toBe(false)
  })

  it('没有组编号的题就算挨着也不并组', () => {
    const { heads, hidden } = groupAskBlocks([ask('a', null), ask('b', null)])

    expect(heads.size).toBe(0)
    expect(hidden.size).toBe(0)
  })

  it('题签、多选开关从 meta 里读；没写就是没有、单选', () => {
    const withMeta = ask('a', 'g', { header: '统计口径', multi_select: true })
    const bare = ask('b', 'g')

    expect(askHeader(withMeta)).toBe('统计口径')
    expect(askMultiSelect(withMeta)).toBe(true)
    expect(askHeader(bare)).toBe('')
    expect(askMultiSelect(bare)).toBe(false)
    expect(askGroupKey(bare)).toBe('g')
    expect(askGroupKey(plain('x'))).toBeNull()
  })
})
