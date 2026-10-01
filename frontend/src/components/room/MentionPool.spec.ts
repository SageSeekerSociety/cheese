/** @ 候选：这间房里的人和 AI 队友，加上项目里还没进这间房的人。没坐在这间房里的
 * AI 队友不在里面——在这里 @ 它什么也不会发生。
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
        { member_handle: 'cheese-planner', name: '规划师', agent: true },
      ],
      total: 2,
    })),
  }
})

import { useRoomRoster } from './composables/useRoomRoster'

import { setLocale } from '@/i18n'

// These assertions read the Chinese copy.
beforeEach(() => {
  setLocale('zh-CN')
})

const MEMBERS: ProjectMemberRow[] = [
  { user_handle: 'alice', name: 'Alice', source: 'owner' },
  { user_handle: 'bob', name: 'Bob', source: 'team' },
  { user_handle: 'cheese-planner', name: '规划师', agent: true },
  { user_handle: 'cheese-reviewer', name: '审稿人', agent: true },
] as ProjectMemberRow[]

describe('@ 候选', () => {
  it('列出这间房里的 AI 队友和还没进来的人，不列没坐在这里的 AI 队友', async () => {
    const r = useRoomRoster({
      topic: () => ({ id: 't1' }) as Topic,
      members: () => MEMBERS,
      author: 'alice',
      onError: () => {},
    })
    // 名册是异步拉的：等它落进来。
    await new Promise((resolve) => setTimeout(resolve))
    const handles = r.mentionPool.value.map((p) => p.handle)
    expect(handles).toContain('cheese-planner')
    expect(handles).toContain('bob')
    expect(handles).not.toContain('cheese-reviewer')
  })

  it('不在这个话题里的人挂「不在话题中」，话题里的人不挂', async () => {
    const r = useRoomRoster({
      topic: () => ({ id: 't1' }) as Topic,
      members: () => MEMBERS,
      author: 'alice',
      onError: () => {},
    })
    await new Promise((resolve) => setTimeout(resolve))
    const outside = Object.fromEntries(r.mentionPool.value.map((p) => [p.handle, !!p.outsideTopic]))
    expect(outside).toEqual({ alice: false, 'cheese-planner': false, bob: true })
  })

  it('名册还没到时谁也不挂：那一刻说不准谁在谁不在', () => {
    const r = useRoomRoster({
      topic: () => ({ id: 't1' }) as Topic,
      members: () => MEMBERS,
      author: 'alice',
      onError: () => {},
    })
    expect(r.mentionPool.value.some((p) => p.outsideTopic)).toBe(false)
  })
})
