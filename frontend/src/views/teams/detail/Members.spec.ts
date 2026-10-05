import type { Component } from 'vue'
import type { Team } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const replace = vi.fn()
const route = { params: { handle: 'crew' }, query: {} as Record<string, string> }
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRoute: () => route,
  useRouter: () => ({ replace }),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMembers: vi.fn(async () => ({ data: { members: [] } })),
    listTeamJoinRequests: vi.fn(async () => ({ data: { applications: [] } })),
    listTeamInvitations: vi.fn(async () => ({ data: { invitations: [] } })),
    approveJoinRequest: vi.fn(),
    removeMember: vi.fn(),
    createInvitation: vi.fn(),
  },
}))
const lookupUser = vi.fn()
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  lookupUser: (...a: unknown[]) => lookupUser(...a),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 确认框回什么由这一格决定：`wait` 解出真就是人点了确定。
const dialogMock = vi.hoisted(() => ({ confirm: vi.fn() }))
vi.mock('@/plugins/dialog', async () => ({
  ...(await vi.importActual<typeof import('@/plugins/dialog')>('@/plugins/dialog')),
  useDialog: () => ({ confirm: dialogMock.confirm }),
}))

import Members from './Members.vue'

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { TeamsApi } from '@/network/api/teams'

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  // The invite dialog is an overlay, and Vuetify positions overlays against it.
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  route.query = {}
  vi.clearAllMocks()
  // 默认「点了确定」：取消那一格在下面的用例里单独摆。
  dialogMock.confirm.mockImplementation(() => ({ wait: async () => true }))
})

function mount(overrides: Partial<Team>) {
  setLocale('zh-CN')
  const team = ref({
    id: 7,
    name: 'Cheese 核心组',
    intro: '',
    avatarId: 1,
    owner: { id: 1 },
    // 第 4 个管理员不在 examples 里：只剩 role 能说明他是管理员。
    admins: { total: 4, examples: [] },
    members: { total: 0, examples: [] },
    visibility: 'public',
    ...overrides,
  } as unknown as Team)
  return render(Members as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: team },
      stubs: { TeamJoinLinkCard: { template: '<section>小队链接管理</section>' } },
    },
  })
}

describe('who manages the team link', () => {
  it('an admin who is not among the listed examples', async () => {
    mount({ role: 'ADMIN' })
    await screen.findByText('小队链接管理')
  })

  it('not an ordinary member', async () => {
    mount({ role: 'MEMBER' })
    await waitFor(() => screen.getByText('成员列表'))
    expect(screen.queryByText('小队链接管理')).toBeNull()
  })
})

describe('bringing people in', () => {
  it('the owner of a shared team can invite and review requests', async () => {
    mount({ role: 'OWNER' })
    await screen.findByRole('button', { name: /邀请成员/ })
    expect(screen.getByText('加入申请')).toBeTruthy()
    expect(screen.getByText('已发送邀请')).toBeTruthy()
  })
})

describe('under your own name', () => {
  it('there is no one to list or bring in: the page sends you to the projects', async () => {
    replace.mockClear()
    mount({ role: 'OWNER', personal: true, handle: 'linxia' })
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'TeamsDetailDefault', params: { handle: 'linxia' } })
    )
    expect(screen.queryByRole('button', { name: /邀请成员/ })).toBeNull()
  })
})

describe('moving someone out', () => {
  const qinmo = { user: { id: 9, nickname: 'qinmo', username: 'qinmo' }, role: 'MEMBER' }

  it('an admin is asked first, and a yes takes them off the team', async () => {
    vi.mocked(TeamsApi.getMembers).mockResolvedValue({ data: { members: [qinmo] } } as never)
    vi.mocked(TeamsApi.removeMember).mockResolvedValue({ data: {} } as never)
    mount({ role: 'ADMIN' })

    await fireEvent.click(await screen.findByRole('button', { name: '移除成员' }))

    // 移出后他不再能进这个团队：行里的入口是灰的，这一下确认才是红的。
    expect(dialogMock.confirm).toHaveBeenCalledWith('移出后他不再是团队成员，也不再能访问团队的项目。', {
      title: '把「qinmo」移出团队？',
      confirmLabel: '移除成员',
      danger: true,
    })
    await waitFor(() => expect(TeamsApi.removeMember).toHaveBeenCalledWith(7, 9))
  })

  it('a no leaves them where they are', async () => {
    dialogMock.confirm.mockImplementation(() => ({ wait: async () => false }))
    vi.mocked(TeamsApi.getMembers).mockResolvedValue({ data: { members: [qinmo] } } as never)
    mount({ role: 'ADMIN' })

    await fireEvent.click(await screen.findByRole('button', { name: '移除成员' }))

    expect(dialogMock.confirm).toHaveBeenCalledTimes(1)
    expect(TeamsApi.removeMember).not.toHaveBeenCalled()
  })
})

describe('answering a join request', () => {
  it('an approval the server answers with no body counts, and a second click sends nothing', async () => {
    const request = {
      id: 31,
      status: 'PENDING',
      createdAt: Date.now(),
      user: { id: 9, username: 'qinmo', nickname: 'qinmo' },
    }
    let approved = false
    vi.mocked(TeamsApi.listTeamJoinRequests).mockImplementation(
      async () =>
        ({
          data: { applications: [{ ...request, status: approved ? 'APPROVED' : 'PENDING' }] },
        }) as never
    )
    let answer!: () => void
    vi.mocked(TeamsApi.approveJoinRequest).mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = () => {
            approved = true
            // 204：响应体是空串
            resolve('' as never)
          }
        })
    )
    route.query = { tab: 'requests' }
    mount({ role: 'OWNER' })

    const approve = await screen.findByRole('button', { name: /批准/ })
    await fireEvent.click(approve)
    await fireEvent.click(approve)
    answer()

    await waitFor(() => expect(screen.queryByRole('button', { name: /批准/ })).toBeNull())
    expect(TeamsApi.approveJoinRequest).toHaveBeenCalledTimes(1)
    expect(TeamsApi.approveJoinRequest).toHaveBeenCalledWith(7, 31)
  })
})

describe('inviting someone by name', () => {
  async function openInvite() {
    mount({ role: 'OWNER' })
    await fireEvent.click(await screen.findByRole('button', { name: /邀请成员/ }))
    return screen.findByLabelText('用户名或邮箱')
  }

  it('invites the person the username belongs to', async () => {
    lookupUser.mockResolvedValue({ id: 31, handle: 'zhangheng', name: '张衡', avatar_id: null })
    vi.mocked(TeamsApi.createInvitation).mockResolvedValue({ data: {} } as never)
    const field = await openInvite()

    await fireEvent.update(field, 'zhangheng')
    await screen.findByText('张衡', {}, { timeout: 2000 })
    await fireEvent.submit(screen.getByRole('button', { name: '邀请' }).closest('form')!)

    expect(lookupUser).toHaveBeenCalledWith('zhangheng')
    await waitFor(() =>
      expect(TeamsApi.createInvitation).toHaveBeenCalledWith(7, { userId: 31, role: 'MEMBER', message: undefined })
    )
  })

  it('a username that begins with digits still means that person, not a user id', async () => {
    lookupUser.mockResolvedValue({ id: 58, handle: '2024zhang', name: '张三', avatar_id: null })
    vi.mocked(TeamsApi.createInvitation).mockResolvedValue({ data: {} } as never)
    const field = await openInvite()

    await fireEvent.update(field, '2024zhang')
    await screen.findByText('张三', {}, { timeout: 2000 })
    await fireEvent.submit(screen.getByRole('button', { name: '邀请' }).closest('form')!)

    await waitFor(() => expect(TeamsApi.createInvitation).toHaveBeenCalledTimes(1))
    expect(vi.mocked(TeamsApi.createInvitation).mock.calls[0][1]).toMatchObject({ userId: 58 })
  })

  it('editing the name drops the person found for the old one at once', async () => {
    lookupUser.mockResolvedValue({ id: 31, handle: 'zhang', name: '张', avatar_id: null })
    const field = await openInvite()

    await fireEvent.update(field, 'zhang')
    await screen.findByTestId('found-user', {}, { timeout: 2000 })
    await fireEvent.update(field, 'zhangsan@example.com')
    await fireEvent.submit(screen.getByRole('button', { name: '邀请' }).closest('form')!)

    expect(screen.queryByTestId('found-user')).toBeNull()
    expect(TeamsApi.createInvitation).not.toHaveBeenCalled()
  })

  it('a name nobody has is said in Chinese, and nothing is sent', async () => {
    lookupUser.mockRejectedValue(new ApiError(404, 'No account with that username or email'))
    const field = await openInvite()

    await fireEvent.update(field, 'nobody-here')
    await screen.findByText('找不到这个用户名或邮箱', {}, { timeout: 2000 })
    await fireEvent.submit(screen.getByRole('button', { name: '邀请' }).closest('form')!)

    expect(TeamsApi.createInvitation).not.toHaveBeenCalled()
  })
})
