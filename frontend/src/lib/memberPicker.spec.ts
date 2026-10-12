// 挑人那张单子的两条纯逻辑：怎么按一句话筛、怎么点一下加减。逻辑从组件里拿出来
// 单独测，是因为它和画法无关 —— 组件只负责把它画出来、把结果往上发。
import type { MemberCandidate } from '@/lib/memberPicker'

import { describe, expect, it } from 'vitest'

import { filterCandidates, toggleCandidate } from '@/lib/memberPicker'

function candidate(over: Partial<MemberCandidate> & { handle: string; name: string }): MemberCandidate {
  return { avatarId: null, agent: false, ...over }
}

const LIST: MemberCandidate[] = [
  candidate({ handle: 'alice', name: 'Alice' }),
  candidate({ handle: 'bobby', name: '波比', external: true }),
  candidate({ handle: 'cheese-t1', name: '芝士', agent: true }),
]

describe('filterCandidates', () => {
  it('takes everyone when there is nothing to search with', () => {
    expect(filterCandidates(LIST, '')).toEqual(LIST)
    expect(filterCandidates(LIST, '   ')).toEqual(LIST)
  })

  it('matches on the displayed name, case-insensitively', () => {
    expect(filterCandidates(LIST, 'ali')).toEqual([LIST[0]])
    expect(filterCandidates(LIST, 'ALICE')).toEqual([LIST[0]])
  })

  it('matches on the handle too — you may know a person by @handle', () => {
    expect(filterCandidates(LIST, 'cheese-t1')).toEqual([LIST[2]])
    expect(filterCandidates(LIST, 'bob')).toEqual([LIST[1]])
  })

  it('matches CJK names as written', () => {
    expect(filterCandidates(LIST, '波')).toEqual([LIST[1]])
    expect(filterCandidates(LIST, '芝士')).toEqual([LIST[2]])
  })

  it('keeps nothing when nobody matches', () => {
    expect(filterCandidates(LIST, 'zzz')).toEqual([])
  })

  it('does not mutate the list it was given', () => {
    const before = LIST.slice()
    filterCandidates(LIST, 'ali')
    expect(LIST).toEqual(before)
  })
})

describe('toggleCandidate', () => {
  it('adds a handle that was not chosen', () => {
    expect(toggleCandidate([], 'alice')).toEqual(['alice'])
    expect(toggleCandidate(['bobby'], 'alice')).toEqual(['bobby', 'alice'])
  })

  it('removes a handle that was already chosen', () => {
    expect(toggleCandidate(['alice', 'bobby'], 'alice')).toEqual(['bobby'])
  })

  it('keeps the order of the rest when removing', () => {
    expect(toggleCandidate(['a', 'b', 'c'], 'b')).toEqual(['a', 'c'])
  })

  it('returns a new array, never the same one it was given', () => {
    const chosen = ['alice']
    const next = toggleCandidate(chosen, 'bobby')
    expect(next).not.toBe(chosen)
    expect(chosen).toEqual(['alice'])
  })
})
