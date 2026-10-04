import type { ProjectMemberRow, TopicMemberRow } from '@/cx_types'

import { afterEach, describe, expect, it } from 'vitest'

import { setLocale } from '@/i18n'
import { agentIdentities, agentNames, memberName, teammateName } from '@/lib/agentNames'

afterEach(() => setLocale('zh-CN'))

describe('a teammate nobody has named', () => {
  it('is called by the reader’s word for it, in either language', () => {
    setLocale('en')
    expect(teammateName('芝士', 'default')).toBe('Cheese')
    setLocale('zh-CN')
    expect(teammateName('芝士', 'default')).toBe('芝士')
  })

  it('keeps a name a person gave it, even 芝士', () => {
    setLocale('en')
    expect(teammateName('芝士', 'human')).toBe('芝士')
    expect(memberName({ name: 'Moss', name_source: 'human' })).toBe('Moss')
    expect(memberName({ name: '李华' })).toBe('李华')
  })

  it('is named the reader’s way wherever the rosters name it', () => {
    setLocale('en')
    const project = [
      { user_handle: 'cheese-a1', instance_handle: 'cheese', agent: true, name: '芝士', name_source: 'default' },
    ] as ProjectMemberRow[]
    const room = [{ member_handle: 'cheese-b2', agent: true, name: '芝士', name_source: 'default' }] as TopicMemberRow[]

    const names = agentNames(room, project)

    expect([names.get('cheese-a1'), names.get('cheese'), names.get('cheese-b2')]).toEqual([
      'Cheese',
      'Cheese',
      'Cheese',
    ])
  })
})

describe('a teammate’s two handles', () => {
  const project = [
    { user_handle: 'cheese-a1', instance_handle: 'cheese', agent: true, name: '小知', name_source: 'human' },
    { user_handle: 'cheese-b2', instance_handle: 'cheese-kimi', agent: true, name: '芝士K', name_source: 'human' },
  ] as ProjectMemberRow[]

  it('name one person, so neither is drawn as two', () => {
    const identities = agentIdentities([], project)

    expect(identities.get('cheese')).toBe('cheese-a1')
    expect(identities.get('cheese-kimi')).toBe('cheese-b2')
    expect(identities.get('cheese-a1')).toBe('cheese-a1')
  })
})
