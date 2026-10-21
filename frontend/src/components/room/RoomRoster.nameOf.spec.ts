/** 一个 handle 在界面上叫什么。反应条列「谁点了」用它——不能拿 mentionNames 那张
 *  @ 渲染表顶：它带着 all/here 两个保留键，一个名字叫 here 的人会被写成「在线成员」。 */
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listTopicMembers: vi.fn(async () => ({
      data: [
        { member_handle: 'here', name: '何瑞', agent: false },
        { member_handle: 'cheese-planner', name: '规划师', agent: true, name_source: 'human' },
      ],
      total: 2,
    })),
  }
})

import { useRoomRoster } from './composables/useRoomRoster'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const MEMBERS: ProjectMemberRow[] = [{ user_handle: 'here', name: '何瑞', source: 'owner' }] as ProjectMemberRow[]

function roster() {
  return useRoomRoster({
    topic: () => ({ id: 't1' }) as Topic,
    members: () => MEMBERS,
    author: 'here',
    onError: () => {},
  })
}

describe('handle → 界面上的名字', () => {
  it('名字恰好叫 here 的人显示他的名字，不是「在线成员」', async () => {
    const r = roster()
    await new Promise((resolve) => setTimeout(resolve))
    expect(r.nameOfHandle('here')).toBe('何瑞')
  })

  it('队友写房间里那个名字；认不出的队友写「芝士」，不写座位 handle', async () => {
    const r = roster()
    await new Promise((resolve) => setTimeout(resolve))
    expect(r.nameOfHandle('cheese-planner')).toBe('规划师')
    expect(r.nameOfHandle('cheese-gone')).toBe('芝士')
  })

  it('名册上找不到的人照旧显示 handle', async () => {
    const r = roster()
    await new Promise((resolve) => setTimeout(resolve))
    expect(r.nameOfHandle('gone-user')).toBe('gone-user')
  })
})
