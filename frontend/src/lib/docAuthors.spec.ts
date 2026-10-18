// 文档的作者：人自己建的只有他；让 AI 队友写的，是让它写的人和那位队友并列，人在前；
// 平台建的没有作者。
import type { ProjectMemberRow } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { docAuthors } from './docAuthors'

const members: ProjectMemberRow[] = [
  { user_handle: 'alice', name: '张三', name_source: 'human' },
  { user_handle: 'cheese-ab12', name: '评审', name_source: 'human', agent: true },
]

describe('文档的作者', () => {
  it('人自己建的文档只有他一个作者', () => {
    expect(docAuthors({ created_by: 'alice', drafted_by: null }, members, '芝士')).toEqual([
      { handle: 'alice', name: '张三', avatar: null, agent: false },
    ])
  })

  it('让 AI 队友写的文档，让它写的人和那位队友并列，人在前', () => {
    const authors = docAuthors({ created_by: 'alice', drafted_by: 'cheese-ab12' }, members, '芝士')
    expect(authors.map((a) => [a.name, a.agent])).toEqual([
      ['张三', false],
      ['评审', true],
    ])
  })

  it('不知道是谁让 AI 队友写的，就只写那位队友', () => {
    const authors = docAuthors({ created_by: null, drafted_by: 'cheese-ff00' }, members, '芝士')
    expect(authors.map((a) => [a.name, a.agent])).toEqual([['芝士', true]])
  })

  it('平台建的文档没有作者', () => {
    expect(docAuthors({ created_by: null, drafted_by: null }, members, '芝士')).toEqual([])
  })
})
