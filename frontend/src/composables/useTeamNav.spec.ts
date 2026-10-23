// 首页那一格目录的接线：名单怎么读、点下去真做什么、成了或没成之后画面怎么变。
//
// 挂的是真接线（侧栏那一份容器），因为这一层的行为只有和画面接上才有意义 —— 光调
// `useTeamNav()` 得到的是一堆 ref，看不出「这一行有没有消失」。抽屉本身和试的东西无关，
// 换成一个只画 slot 的壳。
import type { Component } from 'vue'
import type { Team } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getMyTeams = vi.fn()
const removeMember = vi.fn()
const del = vi.fn()
const getMembers = vi.fn()
const transferOwner = vi.fn()
const update = vi.fn()
vi.mock('@/api/teams', () => ({
  TeamsApi: {
    getMyTeams: () => getMyTeams(),
    removeMember: (...args: unknown[]) => removeMember(...args),
    del: (...args: unknown[]) => del(...args),
    getMembers: (...args: unknown[]) => getMembers(...args),
    transferOwner: (...args: unknown[]) => transferOwner(...args),
    update: (...args: unknown[]) => update(...args),
  },
}))
const createAvatar = vi.fn()
vi.mock('@/api/avatars', () => ({ createAvatar: (...args: unknown[]) => createAvatar(...args) }))
const spaceList = vi.fn()
const spaceJoin = vi.fn()
const spaceApplications = vi.fn()
const spaceCreate = vi.fn()
vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    list: (...args: unknown[]) => spaceList(...args),
    join: (...args: unknown[]) => spaceJoin(...args),
    applications: (...args: unknown[]) => spaceApplications(...args),
    create: (...args: unknown[]) => spaceCreate(...args),
  },
}))
const confirm = vi.fn()
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: (...a: unknown[]) => ({ wait: () => confirm(...a) }) }),
}))
vi.mock('@/services/account', () => ({ default: { user: { id: 42 } } }))
const refreshProjects = vi.fn()
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ refreshProjects }) }))
vi.mock('@/services/ErrorHandler', () => ({
  default: { withErrorHandling: async (fn: () => Promise<unknown>) => fn().catch(() => undefined) },
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn() } }))

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'
import { queryClient } from '@/query/client'
import HomeSidebar from '@/views/home/HomeSidebar.vue'

const blank = { template: '<div />' }

function team(handle: string, name: string, role: Team['role'] = 'MEMBER'): Team {
  return {
    id: [...handle].reduce((n, c) => n + c.charCodeAt(0), 0),
    handle,
    name,
    intro: '',
    avatarId: 1,
    role,
    owner: { id: 1 } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 1, examples: [] },
  }
}

const mounted: { router?: ReturnType<typeof createRouter> } = {}

async function mount(path = '/inbox') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/inbox', name: 'inbox', component: blank },
      // 侧栏里那份目录（`HomeNav.vue`）多了一行通往「市场」：名字不在表里，带 `to`
      // 的行会当场抛，整个侧栏就渲染不出来。
      { path: '/market', name: 'market', component: blank },
      { path: '/space-applications/:spaceId', name: 'HomeSpaceApplication', component: blank },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: blank },
      { path: '/teams/explore', name: 'HomeTeamsExplore', component: blank },
      { path: '/users/settings/compute', name: 'UserSettingsCompute', component: blank },
      {
        path: '/teams/:handle',
        component: { template: '<router-view />' },
        children: [
          { path: '', name: 'TeamsDetailDefault', component: blank },
          { path: 'members', name: 'TeamsDetailMembers', component: blank },
          { path: 'settings', name: 'TeamsDetailSettings', component: blank },
          { path: 'profile', name: 'TeamsDetailProfile', component: blank },
          { path: 'library', name: 'TeamsDetailLibrary', component: blank },
          { path: 'compute', name: 'TeamsDetailCompute', component: blank },
          { path: 'models', name: 'TeamsDetailModels', component: blank },
          { path: 'credits', name: 'TeamsDetailCredits', component: blank },
        ],
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  mounted.router = router
  return render(HomeSidebar as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives }), router],
      stubs: { SecondaryNavigation: { template: '<div><slot /></div>' } },
    },
  })
}

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  // 名单现在住在全站共用的查询缓存里：上一个用例读到的不能带到这一个。
  queryClient.clear()
  for (const fn of [
    getMyTeams,
    removeMember,
    del,
    getMembers,
    transferOwner,
    update,
    createAvatar,
    spaceList,
    spaceJoin,
    spaceApplications,
    spaceCreate,
    confirm,
    refreshProjects,
  ])
    fn.mockReset()
  getMyTeams.mockResolvedValue({ teams: [team('crew', '知是开发组'), team('lab', '数据课第三组')] })
  spaceList.mockResolvedValue({ spaces: [{ id: 3, name: '数据分析课', avatarId: 9 }] })
  spaceApplications.mockResolvedValue({ items: [] })
})
afterEach(cleanup)

describe('目录的名单', () => {
  it('我所在的团队和我加入的空间都读来就画出来', async () => {
    await mount()
    expect(await screen.findByText('知是开发组')).toBeTruthy()
    const spaceRow = (await screen.findByText('数据分析课')).closest('.v-list-item')
    expect(spaceRow?.querySelector('.home-nav__mark img')?.getAttribute('src')).toContain('/avatars/9')
    expect(getMyTeams).toHaveBeenCalled()
  })

  // 从空间或项目回到首页时这块侧栏会卸载重挂。旧名单要当场画出来，不让屏幕先空
  // 一下；同时还要照旧去问服务器，别让目录看不见时删掉、改名的空间被缓存盖住。
  it('回到首页时先画上一次的名单，同时再到服务器问一次', async () => {
    const first = await mount()
    await screen.findByText('知是开发组')
    first.unmount()

    let answerTeams: (value: unknown) => void = () => {}
    getMyTeams.mockImplementationOnce(() => new Promise((resolve) => (answerTeams = resolve)))
    await mount()

    // 请求还没回来，上一次的名单已经在屏幕上了。
    expect(screen.getByText('知是开发组')).toBeTruthy()
    expect(screen.getByText('数据分析课')).toBeTruthy()
    expect(getMyTeams).toHaveBeenCalledTimes(2)

    // 服务器说名单变了：这一次回答会把它换掉。
    answerTeams({ teams: [team('fresh', '刚加入的团队')] })
    expect(await screen.findByText('刚加入的团队')).toBeTruthy()
  })

  // 侧栏跨页面一直挂着：名单要是只在挂载时读一次，刚建的、刚被批准加入的团队
  // 都得整页刷新才出现。
  it('刚建好的团队，跳过去的那一下就出现在侧栏里', async () => {
    await mount('/teams/explore')
    await screen.findByText('知是开发组')
    getMyTeams.mockResolvedValue({
      teams: [team('crew', '知是开发组'), team('lab', '数据课第三组'), team('fresh', '刚建的团队', 'OWNER')],
    })
    await mounted.router!.push('/teams/fresh')
    expect(await screen.findByText('刚建的团队')).toBeTruthy()
  })

  it('在别处被批准加入的团队，回到这个窗口就出现在侧栏里', async () => {
    await mount()
    await screen.findByText('知是开发组')
    getMyTeams.mockResolvedValue({
      teams: [team('crew', '知是开发组'), team('lab', '数据课第三组'), team('joined', '刚加入的团队')],
    })
    window.dispatchEvent(new Event('focus'))
    expect(await screen.findByText('刚加入的团队')).toBeTruthy()
  })

  it('读不到名单就只是空着，不把这一页弄成出错', async () => {
    getMyTeams.mockRejectedValue(new Error('offline'))
    await mount()
    await waitFor(() => expect(getMyTeams).toHaveBeenCalled())
    expect(screen.queryByText('知是开发组')).toBeNull()
    expect(screen.getByText('新建或加入团队')).toBeTruthy()
  })
})

describe('退出团队', () => {
  it('确认了才退：这一行消失，项目清单跟着刷新', async () => {
    confirm.mockResolvedValue(true)
    removeMember.mockImplementation(async () => {
      getMyTeams.mockResolvedValue({ teams: [team('crew', '知是开发组')] })
      return {}
    })
    await mount('/teams/lab')
    await fireEvent.contextMenu(await screen.findByLabelText('收起 数据课第三组'))
    await fireEvent.click(await screen.findByText('退出团队'))

    await waitFor(() => expect(removeMember).toHaveBeenCalledWith(team('lab', '').id, 42))
    await waitFor(() => expect(mounted.router!.currentRoute.value.fullPath).toBe('/inbox'))
    await waitFor(() => expect(screen.queryByText('数据课第三组')).toBeNull())
    expect(refreshProjects).toHaveBeenCalled()
  })

  it('取消确认就什么都不做', async () => {
    confirm.mockResolvedValue(false)
    await mount()
    await fireEvent.contextMenu(await screen.findByLabelText('展开 数据课第三组'))
    await fireEvent.click(await screen.findByText('退出团队'))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(removeMember).not.toHaveBeenCalled()
    expect(screen.getByText('数据课第三组')).toBeTruthy()
  })

  it('退出时还在路上的那次重读回来了，不会把这个团队又画回去', async () => {
    confirm.mockResolvedValue(true)
    removeMember.mockResolvedValue({})
    await mount()
    await screen.findByText('数据课第三组')
    let answerOldRead: (value: unknown) => void = () => {}
    getMyTeams.mockImplementationOnce(() => new Promise((resolve) => (answerOldRead = resolve)))
    await mounted.router!.push('/teams/crew')

    await fireEvent.contextMenu(await screen.findByLabelText('展开 数据课第三组'), { clientX: 40, clientY: 80 })
    await fireEvent.click(await screen.findByText('退出团队'))
    await waitFor(() => expect(screen.queryByText('数据课第三组')).toBeNull())

    answerOldRead({ teams: [team('crew', '知是开发组'), team('lab', '数据课第三组')] })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByText('数据课第三组')).toBeNull()
  })
})

describe('解散团队', () => {
  it('名字打对了才发得出去，成了这一行消失', async () => {
    getMyTeams.mockResolvedValue({ teams: [team('crew', '知是开发组', 'OWNER')] })
    del.mockResolvedValue({})
    await mount()
    await fireEvent.contextMenu(await screen.findByLabelText('展开 知是开发组'))
    await fireEvent.click(await screen.findByText('解散团队'))
    await fireEvent.update(await screen.findByLabelText('输入团队名「知是开发组」确认'), '知是开发组')
    await waitFor(() => expect(screen.getByRole('button', { name: '解散团队' }).hasAttribute('disabled')).toBe(false))
    await fireEvent.click(screen.getByRole('button', { name: '解散团队' }))

    await waitFor(() => expect(del).toHaveBeenCalledWith(team('crew', '').id))
    await waitFor(() => expect(screen.queryByText('知是开发组')).toBeNull())
  })

  it('被拒的理由留在弹窗里，团队还在', async () => {
    getMyTeams.mockResolvedValue({ teams: [team('crew', '知是开发组', 'OWNER')] })
    del.mockRejectedValue(new Error('团队里还有没归档的项目，把它们都归档后才能解散'))
    await mount()
    await fireEvent.contextMenu(await screen.findByLabelText('展开 知是开发组'))
    await fireEvent.click(await screen.findByText('解散团队'))
    await fireEvent.update(await screen.findByLabelText('输入团队名「知是开发组」确认'), '知是开发组')
    await fireEvent.click(screen.getByRole('button', { name: '解散团队' }))
    expect(await screen.findByText('团队里还有没归档的项目，把它们都归档后才能解散')).toBeTruthy()
    expect(screen.getAllByText('知是开发组').length).toBeGreaterThan(0)
  })
})

describe('转让团队', () => {
  it('名单里没有我自己；交出去之后我成了管理员，就能退出了', async () => {
    getMyTeams.mockResolvedValue({ teams: [team('crew', '知是开发组', 'OWNER')] })
    getMembers.mockResolvedValue({
      members: [
        { role: 'OWNER', user: { id: 42, username: 'me', nickname: '我', avatarId: 0 } },
        { role: 'MEMBER', user: { id: 7, username: 'mate', nickname: '小王', avatarId: 0 } },
      ],
    })
    transferOwner.mockResolvedValue({ team: team('crew', '知是开发组') })
    await mount()
    await fireEvent.click(await screen.findByLabelText('团队操作'))
    await fireEvent.click(await screen.findByText('转让团队'))
    await fireEvent.click(await screen.findByText('小王'))
    expect(screen.queryByText('我')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '转让团队' }))
    await waitFor(() => expect(transferOwner).toHaveBeenCalledWith(team('crew', '').id, 7))

    await fireEvent.click(await screen.findByLabelText('团队操作'))
    // 「退出团队」管理员和普通成员都有，分不出降级成没成：管理员那两条菜单项才是刀口。
    expect(await screen.findByText('邀请成员')).toBeTruthy()
    expect(screen.getByText('退出团队')).toBeTruthy()
  })

  // 名单这一格的状态活着的地方（composable）比弹窗长命：关掉甲队的弹窗不会取消那次
  // 读。为乙队打开时甲队的人晚回来，就会画到乙队头上——人在乙队的名单里挑到甲队的人。
  it('换了团队再打开，上一个团队迟到的名单不会画到这一个上', async () => {
    getMyTeams.mockResolvedValue({
      teams: [team('crew', '知是开发组', 'OWNER'), team('lab', '数据课第三组', 'OWNER')],
    })
    let answerCrew: (value: unknown) => void = () => {}
    getMembers
      .mockImplementationOnce(() => new Promise((resolve) => (answerCrew = resolve)))
      .mockResolvedValueOnce({
        members: [{ role: 'MEMBER', user: { id: 7, username: 'mate', nickname: '小王', avatarId: 0 } }],
      })
    await mount()

    const more = await screen.findAllByLabelText('团队操作')
    await fireEvent.click(more[0])
    await fireEvent.click(await screen.findByRole('menuitem', { name: '转让团队' }))
    await fireEvent.click(await screen.findByRole('button', { name: '取消' }))

    await fireEvent.click(more[1])
    await fireEvent.click(await screen.findByRole('menuitem', { name: '转让团队' }))
    expect(await screen.findByText('小王')).toBeTruthy()

    answerCrew({ members: [{ role: 'MEMBER', user: { id: 8, username: 'other', nickname: '小李', avatarId: 0 } }] })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByText('小李')).toBeNull()
    expect(screen.getByText('小王')).toBeTruthy()
  })

  // 同上，只是甲队那次读是**失败**着晚回来的：失败的理由同样只能落回它自己那一次。
  it('上一个团队读失败得晚，理由不会画到这一个团队的弹窗上', async () => {
    getMyTeams.mockResolvedValue({
      teams: [team('crew', '知是开发组', 'OWNER'), team('lab', '数据课第三组', 'OWNER')],
    })
    let failCrew: (reason: unknown) => void = () => {}
    getMembers
      .mockImplementationOnce(() => new Promise((_resolve, reject) => (failCrew = reject)))
      .mockResolvedValueOnce({
        members: [{ role: 'MEMBER', user: { id: 7, username: 'mate', nickname: '小王', avatarId: 0 } }],
      })
    await mount()

    const more = await screen.findAllByLabelText('团队操作')
    await fireEvent.click(more[0])
    await fireEvent.click(await screen.findByRole('menuitem', { name: '转让团队' }))
    await fireEvent.click(await screen.findByRole('button', { name: '取消' }))

    await fireEvent.click(more[1])
    await fireEvent.click(await screen.findByRole('menuitem', { name: '转让团队' }))
    expect(await screen.findByText('小王')).toBeTruthy()

    failCrew(new Error('甲队读不到'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByText('甲队读不到')).toBeNull()
    expect(screen.getByText('小王')).toBeTruthy()
  })
})

describe('手机上', () => {
  // 手机上没有常驻侧栏：同一份目录是底栏「首页」那一格的整页（HomeHub）。侧栏容器照样
  // 挂在那里，这一份没人看，就不该去读后端——更不该每次换页面、每次拿回焦点再读一回。
  it('侧栏那一份不读名单和空间', async () => {
    vi.stubGlobal('innerWidth', 390)
    try {
      await mount()
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(getMyTeams).not.toHaveBeenCalled()
      expect(spaceList).not.toHaveBeenCalled()

      await mounted.router!.push('/spaces')
      window.dispatchEvent(new Event('focus'))
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(getMyTeams).not.toHaveBeenCalled()
    } finally {
      vi.stubGlobal('innerWidth', 1024)
    }
  })

  // 反向的一半：从窄变宽时侧栏才露出来，此前一次没读过，得在这里补上。团队名单有换页面
  // 和拿回焦点两条兜底，空间列表没有——少了这一下，这个会话里的空间就一直是空的。
  it('从窄变宽，侧栏露出来时把名单和空间补读一次', async () => {
    vi.stubGlobal('innerWidth', 390)
    try {
      await mount()
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(getMyTeams).not.toHaveBeenCalled()

      vi.stubGlobal('innerWidth', 1024)
      window.dispatchEvent(new Event('resize'))
      await waitFor(() => expect(getMyTeams).toHaveBeenCalledTimes(1))
      expect(spaceList).toHaveBeenCalledTimes(1)
    } finally {
      vi.stubGlobal('innerWidth', 1024)
    }
  })
})

describe('用邀请码加入空间', () => {
  async function openJoin() {
    await mount()
    await fireEvent.click(await screen.findByText('用邀请码加入'))
    return screen.findByLabelText('邀请码')
  }

  it('成了就进那个空间，空间名单跟着重读', async () => {
    spaceJoin.mockResolvedValue({ space: { id: 9 } })
    const code = await openJoin()
    await fireEvent.update(code, 'abc123')
    await fireEvent.click(screen.getByRole('button', { name: '加入' }))

    await waitFor(() => expect(spaceJoin).toHaveBeenCalledWith({ code: 'abc123' }))
    await waitFor(() => expect(mounted.router!.currentRoute.value.fullPath).toBe('/spaces/9/tasks'))
    expect(spaceList).toHaveBeenCalledTimes(2)
  })

  it('没加进去的理由留在弹窗里，人还在原处', async () => {
    spaceJoin.mockRejectedValue(new Error('bad code'))
    const code = await openJoin()
    await fireEvent.update(code, 'nope')
    await fireEvent.click(screen.getByRole('button', { name: '加入' }))

    expect(await screen.findByText('邀请码无效、已过期或已达使用上限')).toBeTruthy()
    expect(mounted.router!.currentRoute.value.fullPath).toBe('/inbox')
  })
})

describe('我建的空间还没过审', () => {
  const application = (id: number, name: string, reviewStatus: string) => ({ id, name, intro: '', reviewStatus })

  it('待审核和被驳回的也在空间那一组里，标着是哪一种；过审的不重复', async () => {
    spaceApplications.mockResolvedValue({
      items: [
        application(3, '数据分析课', 'APPROVED'),
        application(8, '操作系统 2026 秋', 'PENDING'),
        application(9, '量子计算导论', 'REJECTED'),
      ],
    })
    await mount()
    expect(await screen.findByText('操作系统 2026 秋')).toBeTruthy()
    expect(screen.getByText('待审核')).toBeTruthy()
    expect(screen.getByText('已驳回')).toBeTruthy()
    expect(screen.getAllByText('数据分析课')).toHaveLength(1)
  })

  it('点还没过审的那一行，进的是这份申请', async () => {
    spaceApplications.mockResolvedValue({ items: [application(9, '量子计算导论', 'REJECTED')] })
    await mount()
    await fireEvent.click(await screen.findByText('量子计算导论'))
    await waitFor(() => expect(mounted.router!.currentRoute.value.fullPath).toBe('/space-applications/9'))
  })

  it('申请读不到时，加入的空间照样列出来', async () => {
    spaceApplications.mockRejectedValue(new Error('offline'))
    await mount()
    expect(await screen.findByText('数据分析课')).toBeTruthy()
  })
})

describe('新建空间', () => {
  it('提交以后落到这份申请上', async () => {
    spaceCreate.mockResolvedValue({ space: { id: 12 }, inviteCode: null })
    await mount()
    await fireEvent.click(await screen.findByText('新建空间'))
    await fireEvent.update(await screen.findByLabelText('空间名称'), '  编译原理 2026 秋 ')
    await fireEvent.click(screen.getByRole('button', { name: '提交审核' }))

    await waitFor(() =>
      expect(spaceCreate).toHaveBeenCalledWith({ name: '编译原理 2026 秋', intro: '', avatarId: undefined })
    )
    await waitFor(() => expect(mounted.router!.currentRoute.value.fullPath).toBe('/space-applications/12'))
  })

  it('没建成的那一句留在弹窗里，人还在原处', async () => {
    spaceCreate.mockRejectedValue(new Error('offline'))
    await mount()
    await fireEvent.click(await screen.findByText('新建空间'))
    await fireEvent.update(await screen.findByLabelText('空间名称'), '编译原理')
    await fireEvent.click(screen.getByRole('button', { name: '提交审核' }))

    expect(await screen.findByText('创建失败，稍后重试')).toBeTruthy()
    expect(mounted.router!.currentRoute.value.fullPath).toBe('/inbox')
  })
})
