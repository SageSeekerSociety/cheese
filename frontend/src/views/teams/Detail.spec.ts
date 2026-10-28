import type { Component } from 'vue'
import type { Team } from '@/types'

import { nextTick, reactive } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const detailByHandle = vi.fn()
const join = vi.fn()
vi.mock('@/api/teams', () => ({
  TeamsApi: {
    detailByHandle: (...args: unknown[]) => detailByHandle(...args),
    join: (...args: unknown[]) => join(...args),
  },
}))
const route = reactive({
  params: { handle: 'crew' } as Record<string, string>,
  name: 'TeamsDetailDefault',
  matched: [] as { meta: Record<string, unknown> }[],
})
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }))

import Detail from './Detail.vue'

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'

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
      plugins: [createVuetify({ components, directives }), createPinia()],
      stubs: {
        NavLink: { template: '<a><slot /></a>' },
        RouterView: { template: '<div>成员工作区内容</div>' },
      },
    },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  route.params = { handle: 'crew' }
  route.name = 'TeamsDetailDefault'
  route.matched = []
  detailByHandle.mockReset().mockResolvedValue({ team: team() })
  join.mockReset().mockResolvedValue({ team: team({ joinStatus: 'member' }) })
})
afterEach(cleanup)

describe('a team page', () => {
  it('loads the team its address names', async () => {
    mount()
    await screen.findByText('公开小队')
    expect(detailByHandle).toHaveBeenCalledWith('crew')
  })

  it('stays put when the team is renamed, and loads another team when the address changes', async () => {
    detailByHandle.mockResolvedValue({ team: team({ joinStatus: 'member' }) })
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

  it('shows an outsider the profile, not the workspace', async () => {
    mount()
    await screen.findByText('公开小队')
    expect(screen.queryByText('成员工作区内容')).toBeNull()
  })

  it('opens the workspace once the outsider has joined', async () => {
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '加入团队' }))
    await screen.findByText('成员工作区内容')
    expect(join).toHaveBeenCalledWith(7, { message: undefined })
  })

  it('keeps an applicant on the profile until someone approves', async () => {
    detailByHandle.mockResolvedValue({ team: team({ joinApproval: true }) })
    join.mockResolvedValue({ team: team({ joinApproval: true, joinStatus: 'pending' }) })
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '申请加入' }))
    await screen.findByText('已提交申请，等待团队管理员审批')
    expect(screen.queryByText('成员工作区内容')).toBeNull()
  })

  it('gives members the workspace', async () => {
    detailByHandle.mockResolvedValue({ team: team({ joinStatus: 'member' }) })
    mount()
    await screen.findByText('成员工作区内容')
  })

  // 成员、设备、模型、额度和个人、空间、项目的设置一样，是盖在整个窗口上的一层。
  it('opens a member at a settings address in the team settings layer', async () => {
    detailByHandle.mockResolvedValue({ team: team({ joinStatus: 'member' }) })
    route.name = 'TeamsDetailMembers'
    route.matched = [{ meta: {} }, { meta: { settingsOverlay: true } }]
    mount()
    const layer = await screen.findByRole('dialog', { name: '团队设置' })
    expect(layer.textContent).toContain('成员工作区内容')
  })

  it('answers a hidden team the same way as a missing one', async () => {
    detailByHandle.mockRejectedValue(new ApiError(404, 'not found'))
    mount()
    await screen.findByText('找不到这个团队，它可能已解散或不对你公开')
  })

  // 除了 404，其余原来直接抛出去：抛出去就没人接，页面一片空白 ——
  // 「没读到」和「没有这个团队」在屏幕上是同一幅画面，而该做的完全不同。
  it('says a read failed instead of drawing nothing', async () => {
    detailByHandle.mockRejectedValue({ status: 500, message: '服务端打盹了' })
    mount()

    await screen.findByText('团队信息加载失败')
    expect(screen.getByText('服务端打盹了')).toBeTruthy()
    expect(screen.queryByText('找不到这个团队，它可能已解散或不对你公开')).toBeNull()
  })

  it('403 says no access, with no retry', async () => {
    detailByHandle.mockRejectedValue({ status: 403 })
    mount()

    await screen.findByText('你没有权限查看')
    expect(screen.queryByText('重试')).toBeNull()
  })

  it('retry asks the same handle again', async () => {
    detailByHandle.mockRejectedValueOnce({ status: 500 })
    mount()
    await screen.findByText('团队信息加载失败')

    detailByHandle.mockResolvedValue({ team: team({ joinStatus: 'member' }) })
    await fireEvent.click(screen.getByText('重试'))
    await screen.findByText('成员工作区内容')
    expect(screen.queryByText('团队信息加载失败')).toBeNull()
  })
})
