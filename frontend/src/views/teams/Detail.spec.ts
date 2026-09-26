import type { Component } from 'vue'
import type { Team } from '@/types'

import { nextTick, reactive } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const detailByHandle = vi.fn()
const getMembers = vi.fn()
const join = vi.fn()
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    detailByHandle: (...args: unknown[]) => detailByHandle(...args),
    getMembers: (...args: unknown[]) => getMembers(...args),
    join: (...args: unknown[]) => join(...args),
  },
}))
// 侧栏（即便这里被 stub 掉，模块还是会被加载）那头挂着编辑小队资料的弹窗，
// 它会 import 头像接口 —— 而真实的网络客户端一转手就把 src/router 拉进来，
// 在 vue-router 已被整包替掉的这里会炸在 createRouter 上。接口本身不用假装有行为。
vi.mock('@/network/api/avatars', () => ({ AvatarsApi: { createAvatar: vi.fn() } }))
const route = reactive({ params: { handle: 'crew' } as Record<string, string>, name: 'TeamsDetailDefault' })
vi.mock('vue-router', () => ({ useRoute: () => route }))

import Detail from './Detail.vue'

import { setLocale } from '@/i18n'
import { BusinessError } from '@/network/types/error'

function team(overrides: Partial<Team> = {}): Team {
  return {
    id: 7,
    handle: 'crew',
    name: '公开小队',
    intro: '欢迎来玩',
    avatarId: 1,
    owner: { id: 1, nickname: '队长' } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 1, examples: [] },
    visibility: 'public',
    joinStatus: 'none',
    joinApproval: false,
    ...overrides,
  }
}

function mount() {
  return render(Detail as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: {
        DetailSidebar: { template: '<nav>成员工作区侧栏</nav>' },
        RouterView: { template: '<div>成员工作区内容</div>' },
      },
    },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  route.params = { handle: 'crew' }
  detailByHandle.mockReset().mockResolvedValue({ data: { team: team() } })
  getMembers.mockReset().mockResolvedValue({ data: { members: [] } })
  join.mockReset().mockResolvedValue({ data: { team: team({ joinStatus: 'member' }) } })
})
afterEach(cleanup)

describe('a team page', () => {
  it('loads the team its address names', async () => {
    mount()
    await screen.findByText('公开小队')
    expect(detailByHandle).toHaveBeenCalledWith('crew')
  })

  it('stays put when the team is renamed, and loads another team when the address changes', async () => {
    detailByHandle.mockResolvedValue({ data: { team: team({ joinStatus: 'member' }) } })
    mount()
    await screen.findByText('成员工作区内容')
    // The page itself renamed the team and replaced the address with the new handle.
    detailByHandle.mockClear()
    route.params = { handle: 'CREW' }
    await nextTick()
    expect(detailByHandle).not.toHaveBeenCalled()
    route.params = { handle: 'other-crew' }
    await waitFor(() => expect(detailByHandle).toHaveBeenCalledWith('other-crew'))
  })

  it('shows an outsider the profile and reads nothing only members may read', async () => {
    mount()
    await screen.findByText('公开小队')
    expect(screen.queryByText('成员工作区内容')).toBeNull()
    expect(getMembers).not.toHaveBeenCalled()
  })

  it('opens the workspace once the outsider has joined', async () => {
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '加入团队' }))
    await screen.findByText('成员工作区内容')
    expect(join).toHaveBeenCalledWith(7, { message: undefined })
    await waitFor(() => expect(getMembers).toHaveBeenCalledWith(7))
  })

  it('keeps an applicant on the profile until someone approves', async () => {
    detailByHandle.mockResolvedValue({ data: { team: team({ joinApproval: true }) } })
    join.mockResolvedValue({ data: { team: team({ joinApproval: true, joinStatus: 'pending' }) } })
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '申请加入' }))
    await screen.findByText('已提交申请，等待团队管理员审批')
    expect(screen.queryByText('成员工作区内容')).toBeNull()
    expect(getMembers).not.toHaveBeenCalled()
  })

  it('gives members the workspace', async () => {
    detailByHandle.mockResolvedValue({ data: { team: team({ joinStatus: 'member' }) } })
    mount()
    await screen.findByText('成员工作区内容')
    expect(getMembers).toHaveBeenCalledWith(7)
  })

  it('answers a hidden team the same way as a missing one', async () => {
    detailByHandle.mockRejectedValue(new BusinessError('not found', 404))
    mount()
    await screen.findByText('找不到这个团队，它可能已解散或不对你公开')
    expect(getMembers).not.toHaveBeenCalled()
  })
})
