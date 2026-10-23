import type { Component } from 'vue'
import type { Team } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const replace = vi.fn()
const copy = vi.fn()
const route = { name: 'TeamsDetailMembers', params: { handle: 'crew' }, query: {} as Record<string, string> }
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRoute: () => route,
  useRouter: () => ({ replace }),
}))
vi.mock('@/api/teams', () => ({
  TeamsApi: {
    getMembers: vi.fn(async () => ({ members: [] })),
    listTeamJoinRequests: vi.fn(async () => ({ applications: [] })),
    listTeamInvitations: vi.fn(async () => ({ invitations: [] })),
    approveJoinRequest: vi.fn(),
    removeMember: vi.fn(),
    createInvitation: vi.fn(),
    getJoinLink: vi.fn(async () => ({ token: 'first', approval: true })),
    resetJoinLink: vi.fn(async () => ({ token: 'second', approval: true })),
    updateJoinLink: vi.fn(async () => ({ token: 'first', approval: false })),
    update: vi.fn(),
  },
}))
const lookupUser = vi.fn()
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  lookupUser: (...a: unknown[]) => lookupUser(...a),
}))
const inboxListeners = new Set<() => void>()
vi.mock('@/lib/inboxLive', () => ({
  onInboxChange: (fn: () => void) => {
    inboxListeners.add(fn)
    return () => inboxListeners.delete(fn)
  },
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
import { TeamsApi } from '@/api/teams'
import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

beforeAll(() => {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: copy } })
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
  inboxListeners.clear()
  vi.clearAllMocks()
  vi.mocked(TeamsApi.getMembers).mockResolvedValue({ members: [] } as never)
  vi.mocked(TeamsApi.listTeamJoinRequests).mockResolvedValue({ applications: [] } as never)
  vi.mocked(TeamsApi.listTeamInvitations).mockResolvedValue({ invitations: [] } as never)
  // 默认「点了确定」：取消那一格在下面的用例里单独摆。
  dialogMock.confirm.mockImplementation(() => ({ wait: async () => true }))
})

function mount(overrides: Partial<Team>) {
  setLocale('zh-CN')
  const team = ref({
    id: 7,
    handle: 'crew',
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
  const view = render(Members as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: team },
    },
  })
  // 注入的那一份就是页头读的那一份：团队被改过没有，看它。
  return { ...view, team }
}

describe('who manages the team link', () => {
  it('an admin who is not among the listed examples', async () => {
    mount({ role: 'ADMIN' })
    await screen.findByText('加入方式')
  })

  it('not an ordinary member', async () => {
    mount({ role: 'MEMBER' })
    await screen.findByRole('heading', { level: 2, name: '成员' })
    expect(screen.queryByText('加入方式')).toBeNull()
  })
})

// 加入链接是「能把人带进来」的人的东西：读它对普通成员只回 403。取数在这层，所以
// 「谁去读」也断在这层——卡片由 v-if 挡住，取数要一并挡住，权限变动时也一样。
describe('who the join link is read for', () => {
  it('an ordinary member never asks for it', async () => {
    mount({ role: 'MEMBER' })

    await screen.findByRole('heading', { level: 2, name: '成员' })
    expect(TeamsApi.getJoinLink).not.toHaveBeenCalled()
  })

  it('reads it once the reader is given the right to bring people in', async () => {
    const { team } = mount({ role: 'MEMBER' })
    await screen.findByRole('heading', { level: 2, name: '成员' })
    expect(TeamsApi.getJoinLink).not.toHaveBeenCalled()

    team.value.role = 'ADMIN'

    await waitFor(() => expect(TeamsApi.getJoinLink).toHaveBeenCalledWith(7))
    await screen.findByText('加入方式')
  })

  it('drops the link and stops asking for it when the right is taken away', async () => {
    const { team } = mount({ role: 'OWNER' })
    await screen.findByText('加入方式')
    await waitFor(() => expect(TeamsApi.getJoinLink).toHaveBeenCalledTimes(1))

    team.value.role = 'MEMBER'

    await waitFor(() => expect(screen.queryByText('加入方式')).toBeNull())
    expect(TeamsApi.getJoinLink).toHaveBeenCalledTimes(1)
  })
})

// 加入链接那张卡的取数在这层：卡片自己一件都不做，所以行为要在这里断。
describe('running the team link', () => {
  const linkField = () => (screen.getByLabelText('团队链接') as HTMLInputElement).value

  it('hands out a link the reader can copy', async () => {
    mount({ role: 'OWNER' })

    await waitFor(() => expect(linkField()).toBe(`${location.origin}/team-invites/first`))
    await fireEvent.click(screen.getByRole('button', { name: '复制链接' }))

    expect(copy).toHaveBeenCalledWith(`${location.origin}/team-invites/first`)
    await screen.findByRole('button', { name: '已复制' })
  })

  it('hands out a new link when the old one is reset', async () => {
    mount({ role: 'OWNER' })

    await waitFor(() => expect(linkField()).toContain('/team-invites/first'))
    await fireEvent.click(screen.getByRole('button', { name: '重置链接' }))

    await waitFor(() => expect(linkField()).toContain('/team-invites/second'))
    expect(TeamsApi.resetJoinLink).toHaveBeenCalledWith(7)
  })

  it('turns approval off', async () => {
    mount({ role: 'OWNER' })
    const approval = await screen.findByLabelText('加入需要审批')
    await waitFor(() => expect((approval as HTMLInputElement).checked).toBe(true))
    ;(approval as HTMLInputElement).click()

    await waitFor(() => expect(TeamsApi.updateJoinLink).toHaveBeenCalledWith(7, { approval: false }))
    await waitFor(() => expect((screen.getByLabelText('加入需要审批') as HTMLInputElement).checked).toBe(false))
  })

  it('hides the team from search, and the team the page holds follows', async () => {
    const { team } = mount({ role: 'OWNER' })
    vi.mocked(TeamsApi.update).mockResolvedValue({ team: { ...team.value, visibility: 'stealth' } } as never)
    const stealth = (await screen.findByLabelText(/^隐身/)) as HTMLInputElement
    // 读链接那一下期间它按不动：等这一手过去再点。
    await waitFor(() => expect(stealth.disabled).toBe(false))

    stealth.click()

    await waitFor(() => expect(TeamsApi.update).toHaveBeenCalledWith(7, { visibility: 'stealth' }))
    await waitFor(() => expect(team.value.visibility).toBe('stealth'))
  })

  it('says so when the link cannot be read', async () => {
    vi.mocked(TeamsApi.getJoinLink).mockRejectedValue(new Error('boom'))
    mount({ role: 'OWNER' })

    await screen.findByText('操作失败，请重试')
    expect(screen.queryByRole('button', { name: '复制链接' })).toBeNull()
  })
})

describe('bringing people in', () => {
  it('the owner of a shared team can invite, and sees requests and invitations once there are some', async () => {
    vi.mocked(TeamsApi.listTeamJoinRequests).mockResolvedValue({
      applications: [
        { id: 31, status: 'PENDING', createdAt: Date.now(), user: { id: 9, username: 'qinmo', nickname: 'qinmo' } },
      ],
    } as never)
    vi.mocked(TeamsApi.listTeamInvitations).mockResolvedValue({
      invitations: [
        { id: 41, status: 'PENDING', createdAt: Date.now(), user: { id: 11, username: 'frank', nickname: 'frank' } },
      ],
    } as never)
    mount({ role: 'OWNER' })
    await screen.findByRole('button', { name: /邀请成员/ })
    await screen.findByRole('heading', { name: /加入申请/ })
    await screen.findByRole('heading', { name: /已发送邀请/ })
  })

  it('no request and no invitation: those two cards are not drawn', async () => {
    mount({ role: 'OWNER' })
    await screen.findByRole('button', { name: /邀请成员/ })
    await waitFor(() => expect(TeamsApi.listTeamInvitations).toHaveBeenCalled())
    expect(screen.queryByRole('heading', { name: /加入申请/ })).toBeNull()
    expect(screen.queryByRole('heading', { name: /已发送邀请/ })).toBeNull()
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
  const qinmo = { user: { id: 9, nickname: 'qinmo', username: 'qinmo' }, role: 'MEMBER', canRemove: true }

  it('a member the server will not let the reader remove has no 移除成员', async () => {
    // 管理员读到的另一位管理员：服务端说移不了，这一行就不给按钮。
    const peer = { user: { id: 10, nickname: 'zhaoyu', username: 'zhaoyu' }, role: 'ADMIN', canRemove: false }
    vi.mocked(TeamsApi.getMembers).mockResolvedValue({ members: [peer, qinmo] } as never)
    mount({ role: 'ADMIN' })

    await screen.findByRole('button', { name: 'qinmo 的操作' })
    expect(screen.queryByRole('button', { name: 'zhaoyu 的操作' })).toBeNull()
  })

  it('an admin is asked first, and a yes takes them off the team', async () => {
    vi.mocked(TeamsApi.getMembers).mockResolvedValue({ members: [qinmo] } as never)
    vi.mocked(TeamsApi.removeMember).mockResolvedValue(true as never)
    mount({ role: 'ADMIN' })

    await fireEvent.click(await screen.findByRole('button', { name: 'qinmo 的操作' }))
    await fireEvent.click(await screen.findByText('移除成员'))

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
    vi.mocked(TeamsApi.getMembers).mockResolvedValue({ members: [qinmo] } as never)
    mount({ role: 'ADMIN' })

    await fireEvent.click(await screen.findByRole('button', { name: 'qinmo 的操作' }))
    await fireEvent.click(await screen.findByText('移除成员'))

    expect(dialogMock.confirm).toHaveBeenCalledTimes(1)
    expect(TeamsApi.removeMember).not.toHaveBeenCalled()
  })
})

describe('角色说明', () => {
  const qinmo = { user: { id: 9, nickname: 'qinmo', username: 'qinmo' }, role: 'MEMBER' }

  it('成员表里每个人的角色旁写着一句这个角色能做什么', async () => {
    vi.mocked(TeamsApi.getMembers).mockResolvedValue({ members: [qinmo] } as never)
    mount({ role: 'ADMIN' })
    // 每一行都有角色小标，旁边一句这个角色能做什么。
    await screen.findByText('参与团队的项目')
    expect(screen.getByText('普通成员', { selector: '.members-chip' })).toBeTruthy()
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
          applications: [{ ...request, status: approved ? 'APPROVED' : 'PENDING' }],
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

  it('shows the pending request count at once and updates it in real time when the inbox pushes', async () => {
    const first = {
      id: 31,
      status: 'PENDING',
      createdAt: Date.now(),
      user: { id: 9, username: 'shenlan', nickname: 'shenlan' },
    }
    const second = {
      id: 32,
      status: 'PENDING',
      createdAt: Date.now(),
      user: { id: 10, username: 'lz123y', nickname: 'lz123y' },
    }
    let applications = [first]
    vi.mocked(TeamsApi.listTeamJoinRequests).mockImplementation(async () => ({ applications }) as never)

    mount({ role: 'OWNER' })

    const requests = await screen.findByRole('heading', { name: /加入申请/ })
    await waitFor(() => expect(requests.textContent).toContain('1'))
    await screen.findByText('shenlan')

    // 有人新递了一条申请，收件箱连接推来通知：不用刷新页面，列表和数字当场更新。
    applications = [first, second]
    for (const fn of inboxListeners) fn()

    await screen.findByText('lz123y')
    await waitFor(() => expect(requests.textContent).toContain('2'))
  })

  it('does not fetch join requests for a regular member until they become an admin', async () => {
    const { team } = mount({ role: 'MEMBER' })
    for (const fn of inboxListeners) fn()
    expect(TeamsApi.listTeamJoinRequests).not.toHaveBeenCalled()

    team.value = { ...team.value, role: 'ADMIN' }
    await waitFor(() => expect(TeamsApi.listTeamJoinRequests).toHaveBeenCalledWith(7))
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
    vi.mocked(TeamsApi.createInvitation).mockResolvedValue(true as never)
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
    vi.mocked(TeamsApi.createInvitation).mockResolvedValue(true as never)
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

  it('someone already in the team is said so, and cannot be invited again', async () => {
    vi.mocked(TeamsApi.getMembers).mockResolvedValue({
      members: [{ user: { id: 31, nickname: '张衡', username: 'zhangheng' }, role: 'MEMBER' }],
    } as never)
    lookupUser.mockResolvedValue({ id: 31, handle: 'zhangheng', name: '张衡', avatar_id: null })
    const field = await openInvite()

    await fireEvent.update(field, 'zhangheng')
    await screen.findByText('已经在团队里', {}, { timeout: 2000 })
    expect((screen.getByRole('button', { name: '邀请' }) as HTMLButtonElement).disabled).toBe(true)
    await fireEvent.submit(screen.getByRole('button', { name: '邀请' }).closest('form')!)

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
