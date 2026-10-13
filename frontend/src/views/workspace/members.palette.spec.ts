// 命令面板 `@` 选中一位 AI 队友后的「私聊」：私聊按队友自己的 handle 开
// （`agent:<instance_handle>`），名册上那个座位 handle 后端认不出是哪位队友。
import type { Router } from 'vue-router'
import type { SourceContext } from '@/commands/palette/sources'
import type { ProjectMemberRow } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

import source from './members.palette'

import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

vi.mock('@/me', () => ({ myHandle: () => 'lin' }))

const ctx: SourceContext = { projectId: 'p1', router: {} as unknown as Router }

async function mountStore(members: ProjectMemberRow[]) {
  setActivePinia(createPinia())
  seedProjects([])
  seedProject('p1', { topics: [], members, unread: {}, privateUnread: {}, notifyLevels: {} })
  const store = useWorkspaceStore()
  store.openProject('p1')
  await vi.waitFor(() => expect(store.members).toHaveLength(members.length))
}

function dmPeer(handle: string) {
  const item = source.items!(ctx).find((i) => i.id === `member:${handle}`)
  const dm = item?.actions?.().find((a) => a.id === 'member.dm')
  return (dm?.to as { params?: { peer?: string } } | undefined)?.params?.peer
}

describe('命令面板：成员的私聊', () => {
  it('opens a teammate’s private chat by its own handle', async () => {
    await mountStore([
      { user_handle: 'lin', name: '林' },
      { user_handle: 'wang', name: '王' },
      { user_handle: 'cheese-742841cae293', name: '小杉', agent: true, instance_handle: 'xiaoshan' },
    ])

    expect(dmPeer('cheese-742841cae293')).toBe('agent:xiaoshan')
    expect(dmPeer('wang')).toBe('wang')
    expect(dmPeer('lin')).toBeUndefined()
  })
})
