// 哪几张卡是一组：同一条通知（`meta.notice_id`）的几道题挨着排，就是一组，只有组头
// 占一行；单独一道题不进表，照单题的卡片画。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import {
  askAnswered,
  askAsksViewer,
  askGroupFolds,
  askGroupKey,
  askHeader,
  askMultiSelect,
  askWithdrawn,
  groupAskBlocks,
} from './askGroups'

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

  it('问到没问到：点名时只看名单，问所有人、没点名都算问到', () => {
    expect(askAsksViewer(ask('a', 'g', { asked: ['alice'] }), 'alice')).toBe(true)
    expect(askAsksViewer(ask('a', 'g', { asked: ['alice'] }), 'bob')).toBe(false)
    expect(askAsksViewer(ask('a', 'g', { asked: ['alice', 'bob'] }), 'bob')).toBe(true)
    expect(askAsksViewer(ask('a', 'g', { asked: ['alice'], asked_all: true }), 'bob')).toBe(true)
    // 没点名（平台发起的那一轮里问的）：频道里谁都答得上。
    expect(askAsksViewer(ask('a', 'g', { asked: [] }), 'bob')).toBe(true)
    // 还不知道看的人是谁：不替他判断。
    expect(askAsksViewer(ask('a', 'g', { asked: ['alice'] }), null)).toBe(true)
  })

  it('撤回的题整行不画；过期的还画（它还能答）', () => {
    const withdrawn = ask('a', 'g', {
      answer_log: [{ kind: 'withdrawn', by: 'cheese', at: '2026-10-22T00:01:00Z' }],
    })
    const expired = ask('b', 'g', { answer_log: [{ kind: 'expired', by: 'cheese', at: '2026-10-22T00:01:00Z' }] })

    expect(askWithdrawn(withdrawn)).toBe(true)
    expect(askWithdrawn(expired)).toBe(false)
    expect(askWithdrawn(ask('c', 'g'))).toBe(false)
  })

  it('答过就是答过：跳过不算', () => {
    const answered = ask('a', 'g', {
      answer_log: [{ kind: 'option', option: 'A', by: 'alice', at: '2026-10-22T00:01:00Z' }],
    })
    const skipped = ask('b', 'g', {
      answer_log: [{ kind: 'skipped', by: 'alice', at: '2026-10-22T00:01:00Z' }],
    })

    expect(askAnswered(answered)).toBe(true)
    expect(askAnswered(skipped)).toBe(false)
    expect(askAnswered(ask('c', 'g'))).toBe(false)
  })

  it('整组问答完就收成一行；问到还没答的照整张卡，只答了一部分也不收', () => {
    const mineOpen = [ask('a', 'g', { asked: ['me'] }), ask('b', 'g', { asked: ['me'] })]
    const answer = { kind: 'option', option: 'A', by: 'me', at: '2026-10-22T00:01:00Z' }
    const halfDone = [ask('a', 'g', { asked: ['me'], answer_log: [answer] }), ask('b', 'g', { asked: ['me'] })]
    const allDone = [
      ask('a', 'g', { asked: ['me'], answer_log: [answer] }),
      ask('b', 'g', { asked: ['me'], answer_log: [answer] }),
    ]
    const othersDone = [
      ask('a', 'g', { asked: ['alice'], answer_log: [{ ...answer, by: 'alice' }] }),
      ask('b', 'g', { asked: ['alice'], answer_log: [{ ...answer, by: 'alice' }] }),
    ]
    const othersOpen = [ask('a', 'g', { asked: ['alice'] }), ask('b', 'g', { asked: ['alice'] })]

    expect(askGroupFolds(mineOpen, 'me')).toBe(false)
    expect(askGroupFolds(halfDone, 'me')).toBe(false)
    expect(askGroupFolds(allDone, 'me')).toBe(true)
    expect(askGroupFolds(othersOpen, 'me')).toBe(true)
    expect(askGroupFolds(othersDone, 'me')).toBe(true)
    // 还不知道看的人是谁：照整张卡画，不替他收。
    expect(askGroupFolds(mineOpen, null)).toBe(false)
    // 组里每一道都被撤回了：没有人还在等，收起来。
    const allWithdrawn = [
      ask('a', 'g', { asked: ['me'], answer_log: [{ kind: 'withdrawn', by: 'cheese', at: '2026-10-22T00:01:00Z' }] }),
      ask('b', 'g', { asked: ['me'], answer_log: [{ kind: 'withdrawn', by: 'cheese', at: '2026-10-22T00:01:00Z' }] }),
    ]
    expect(askGroupFolds(allWithdrawn, 'me')).toBe(true)
    expect(askGroupFolds([], 'me')).toBe(false)
  })
})
