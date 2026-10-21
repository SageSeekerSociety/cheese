/** Project members can mention any personal agent, including one outside the room.
 *  A private channel is the exception: only agents whose owner is in it. */
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
  { user_handle: 'carol', name: 'Carol', source: 'team' },
  { user_handle: 'cheese-bobs', name: 'Bob的 Claude Code', agent: true, owner_handle: 'bob' },
  { user_handle: 'cheese-alices', name: 'Alice的 Claude Code', agent: true, owner_handle: 'alice' },
  { user_handle: 'cheese-carols', name: 'Carol的 Claude Code', agent: true, owner_handle: 'carol' },
] as ProjectMemberRow[]

async function poolFor(author: string, members: ProjectMemberRow[] = MEMBERS, membersOnly = false): Promise<string[]> {
  const r = useRoomRoster({
    topic: () => ({ id: 't1', members_only: membersOnly }) as Topic,
    members: () => members,
    author,
    onError: () => {},
  })
  await new Promise((resolve) => setTimeout(resolve))
  return r.mentionPool.value.map((p) => p.handle)
}

describe('@ 候选里成员自己的 Claude Code', () => {
  it('项目成员可以提及别人的智能体', async () => {
    expect(await poolFor('alice')).toContain('cheese-bobs')
  })

  it('别人的智能体还没加入频道时也可以提及', async () => {
    expect(await poolFor('bob')).toContain('cheese-alices')
  })

  it('自己的出现，哪怕还没坐进这个频道', async () => {
    expect(await poolFor('alice')).toContain('cheese-alices')
    expect(await poolFor('bob')).toContain('cheese-bobs')
  })

  it('私密频道不列频道外成员的个人 Claude Code', async () => {
    expect(await poolFor('alice', MEMBERS, true)).not.toContain('cheese-carols')
  })

  it('私密频道里，频道成员自己的 Claude Code 照常列出', async () => {
    expect(await poolFor('bob', MEMBERS, true)).toContain('cheese-alices')
  })

  it('公开频道仍然列出频道外成员的个人 Claude Code', async () => {
    expect(await poolFor('alice', MEMBERS, false)).toContain('cheese-carols')
  })
})
