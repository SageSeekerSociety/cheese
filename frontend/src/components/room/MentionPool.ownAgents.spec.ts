/** 成员自己的 Claude Code 只有它答应的人叫得动（#2991）：主人，以及主人放开给项目时
 * 项目里的人。叫不动的不出现——就算它坐在这个频道里。叫得动的还没坐进来也列出，第一次
 * @ 它就入座。
 */
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listTopicMembers: vi.fn(async () => ({
      data: [
        { member_handle: 'alice', name: 'Alice', agent: false },
        { member_handle: 'bob', name: 'Bob', agent: false },
        // bob 的 Claude Code 已经坐在这个频道里。
        { member_handle: 'cheese-bobs', name: 'Bob的 Claude Code', agent: true },
      ],
      total: 3,
    })),
  }
})

import { useRoomRoster } from './composables/useRoomRoster'

import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
})

const MEMBERS: ProjectMemberRow[] = [
  { user_handle: 'alice', name: 'Alice', source: 'owner' },
  { user_handle: 'bob', name: 'Bob', source: 'team' },
  { user_handle: 'cheese-bobs', name: 'Bob的 Claude Code', agent: true, owner_handle: 'bob' },
  { user_handle: 'cheese-alices', name: 'Alice的 Claude Code', agent: true, owner_handle: 'alice' },
] as ProjectMemberRow[]

async function poolFor(author: string, members: ProjectMemberRow[] = MEMBERS): Promise<string[]> {
  const r = useRoomRoster({
    topic: () => ({ id: 't1' }) as Topic,
    members: () => members,
    author,
    onError: () => {},
  })
  await new Promise((resolve) => setTimeout(resolve))
  return r.mentionPool.value.map((p) => p.handle)
}

describe('@ 候选里成员自己的 Claude Code', () => {
  it('别人的不出现，哪怕它坐在这个频道里', async () => {
    expect(await poolFor('alice')).not.toContain('cheese-bobs')
  })

  it('主人放开给项目的，项目里的人都看得到', async () => {
    const opened = MEMBERS.map((m) => (m.user_handle === 'cheese-bobs' ? { ...m, callers: 'project' as const } : m))
    expect(await poolFor('alice', opened)).toContain('cheese-bobs')
  })

  it('自己的出现，哪怕还没坐进这个频道', async () => {
    expect(await poolFor('alice')).toContain('cheese-alices')
    expect(await poolFor('bob')).toContain('cheese-bobs')
  })
})
