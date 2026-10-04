// 首页那一格的目录：团队在原地展开成四样东西，正在看的那个团队一定是展开的；
// 团队行的 ⋯ 和右键：管理员邀请、改资料，不是所有者的能退出团队。
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
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMyTeams: () => getMyTeams(),
    removeMember: (...args: unknown[]) => removeMember(...args),
    del: (...args: unknown[]) => del(...args),
    getMembers: (...args: unknown[]) => getMembers(...args),
    transferOwner: (...args: unknown[]) => transferOwner(...args),
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
vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { list: async () => ({ data: { spaces: [{ id: 3, name: '数据分析课' }] } }) },
}))

import HomeNav from './HomeNav.vue'

import { setLocale } from '@/i18n'

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

async function mount(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/inbox', name: 'inbox', component: blank },
      { path: '/spaces', name: 'HomeSpaces', component: blank },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: blank },
      { path: '/teams/explore', name: 'HomeTeamsExplore', component: blank },
      {
        path: '/teams/:handle',
        component: { template: '<router-view />' },
        children: [
          { path: '', name: 'TeamsDetailDefault', component: blank },
          { path: 'members', name: 'TeamsDetailMembers', component: blank },
          { path: 'knowledge', name: 'TeamsDetailKnowledge', component: blank },
          { path: 'compute', name: 'TeamsDetailCompute', component: blank },
          { path: 'credits', name: 'TeamsDetailCredits', component: blank },
        ],
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  return render(HomeNav as unknown as Component, {
    props: { inbox: true },
    global: {
      plugins: [createVuetify({ components, directives }), router],
      stubs: { TeamProfileEditDialog: true, JoinSpaceDialog: true },
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
  // 团队操作是个 v-menu，打开时要量视口；jsdom 两样都没有。
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
  removeMember.mockReset()
  del.mockReset()
  getMembers.mockReset()
  transferOwner.mockReset()
  confirm.mockReset()
  refreshProjects.mockReset()
  getMyTeams
    .mockReset()
    .mockResolvedValue({ data: { teams: [team('crew', '知是开发组'), team('lab', '数据课第三组')] } })
})
afterEach(cleanup)

const hrefs = () => Array.from(document.querySelectorAll('a')).map((a) => a.getAttribute('href'))

describe('首页目录', () => {
  it('正在看的那个团队展开着，四样东西都点得到；别的团队收着', async () => {
    await mount('/teams/crew/members')
    await screen.findByText('知是开发组')
    await waitFor(() =>
      expect(hrefs()).toEqual(
        expect.arrayContaining(['/teams/crew', '/teams/crew/members', '/teams/crew/knowledge', '/teams/crew/compute'])
      )
    )
    expect(hrefs().some((href) => href?.startsWith('/teams/lab'))).toBe(false)
  })

  it('点一个团队在原地展开，再点收起', async () => {
    await mount('/inbox')
    await fireEvent.click(await screen.findByLabelText('展开 数据课第三组'))
    await waitFor(() => expect(hrefs()).toContain('/teams/lab/members'))
    await fireEvent.click(screen.getByLabelText('收起 数据课第三组'))
    await waitFor(() => expect(hrefs()).not.toContain('/teams/lab/members'))
  })

  it.each([
    ['ADMIN', ['邀请成员', '编辑团队资料', '退出团队']],
    ['MEMBER', ['退出团队']],
    ['OWNER', ['邀请成员', '编辑团队资料', '转让团队', '解散团队']],
  ] as const)('%s 在团队那一行的 ⋯ 里看到的操作', async (role, labels) => {
    getMyTeams.mockResolvedValue({ data: { teams: [team('crew', '知是开发组', role)] } })
    await mount('/inbox')
    await fireEvent.click(await screen.findByLabelText('团队操作'))
    await screen.findByText(labels[0])
    const shown = Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) =>
      el.textContent?.trim()
    )
    expect(shown).toEqual(labels)
  })

  it('右键一个团队弹出同一份操作，确认后退出团队、这一行消失', async () => {
    confirm.mockResolvedValue(true)
    removeMember.mockResolvedValue({})
    await mount('/teams/lab')
    await fireEvent.contextMenu(await screen.findByLabelText('收起 数据课第三组'), { clientX: 40, clientY: 80 })
    await fireEvent.click(await screen.findByText('退出团队'))

    await waitFor(() => expect(removeMember).toHaveBeenCalledWith(team('lab', '').id, 42))
    await waitFor(() => expect(screen.queryByText('数据课第三组')).toBeNull())
    expect(refreshProjects).toHaveBeenCalled()
  })

  it('所有者解散团队：要把团队名打一遍才按得下去，解散后这一行消失', async () => {
    getMyTeams.mockResolvedValue({ data: { teams: [team('crew', '知是开发组', 'OWNER')] } })
    del.mockResolvedValue({})
    await mount('/inbox')
    await fireEvent.contextMenu(await screen.findByLabelText('展开 知是开发组'))
    await fireEvent.click(await screen.findByText('解散团队'))
    const confirmButton = await screen.findByRole('button', { name: '解散团队' })
    const input = screen.getByLabelText('输入团队名「知是开发组」确认')

    await fireEvent.update(input, '知是')
    expect(confirmButton.hasAttribute('disabled')).toBe(true)
    await fireEvent.click(confirmButton)
    expect(del).not.toHaveBeenCalled()

    await fireEvent.update(input, '知是开发组')
    await waitFor(() => expect(confirmButton.hasAttribute('disabled')).toBe(false))
    await fireEvent.click(confirmButton)
    await waitFor(() => expect(del).toHaveBeenCalledWith(team('crew', '').id))
    await waitFor(() => expect(screen.queryByText('知是开发组')).toBeNull())
  })

  it('解散被拒（还有没归档的项目）：理由留在弹窗里，团队还在', async () => {
    getMyTeams.mockResolvedValue({ data: { teams: [team('crew', '知是开发组', 'OWNER')] } })
    del.mockRejectedValue(new Error('团队里还有没归档的项目，把它们都归档后才能解散'))
    await mount('/inbox')
    await fireEvent.contextMenu(await screen.findByLabelText('展开 知是开发组'))
    await fireEvent.click(await screen.findByText('解散团队'))
    await fireEvent.update(await screen.findByLabelText('输入团队名「知是开发组」确认'), '知是开发组')
    await fireEvent.click(await screen.findByRole('button', { name: '解散团队' }))
    expect(await screen.findByText('团队里还有没归档的项目，把它们都归档后才能解散')).toBeTruthy()
    expect(screen.getAllByText('知是开发组').length).toBeGreaterThan(0)
  })

  it('所有者把团队交给一位成员，之后自己就能退出了', async () => {
    getMyTeams.mockResolvedValue({ data: { teams: [team('crew', '知是开发组', 'OWNER')] } })
    getMembers.mockResolvedValue({
      data: {
        members: [
          { role: 'OWNER', user: { id: 42, username: 'me', nickname: '我', avatarId: 0 } },
          { role: 'MEMBER', user: { id: 7, username: 'mate', nickname: '小王', avatarId: 0 } },
        ],
      },
    })
    transferOwner.mockResolvedValue({ data: { team: team('crew', '知是开发组', 'OWNER') } })
    await mount('/inbox')
    await fireEvent.click(await screen.findByLabelText('团队操作'))
    await fireEvent.click(await screen.findByText('转让团队'))
    await fireEvent.click(await screen.findByText('小王'))
    expect(screen.queryByText('我')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '转让团队' }))
    await waitFor(() => expect(transferOwner).toHaveBeenCalledWith(team('crew', '').id, 7))

    await fireEvent.contextMenu(await screen.findByLabelText('展开 知是开发组'))
    expect(await screen.findByText('退出团队')).toBeTruthy()
  })

  it('取消确认就什么都不做', async () => {
    confirm.mockResolvedValue(false)
    await mount('/inbox')
    await fireEvent.contextMenu(await screen.findByLabelText('展开 数据课第三组'))
    await fireEvent.click(await screen.findByText('退出团队'))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(removeMember).not.toHaveBeenCalled()
    expect(screen.getByText('数据课第三组')).toBeTruthy()
  })

  // 自己名下的项目不是一个团队：以自己的昵称单独一行，在「团队」小标题之上；
  // 展开后没有「成员」，也没有团队的 ⋯ 操作。
  it('自己名下在「团队」之上单独一行，没有成员页，也没有团队操作', async () => {
    const own = { ...team('andy', '林夏', 'OWNER'), personal: true }
    getMyTeams.mockResolvedValue({ data: { teams: [own, team('crew', '知是开发组', 'OWNER')] } })
    await mount('/teams/andy')
    await waitFor(() =>
      expect(hrefs()).toEqual(expect.arrayContaining(['/teams/andy', '/teams/andy/knowledge', '/teams/andy/compute']))
    )
    expect(hrefs()).not.toContain('/teams/andy/members')
    // 自己的额度在个人设置里；团队才有「额度」这一页。
    expect(hrefs()).not.toContain('/teams/andy/credits')

    const text = document.body.textContent ?? ''
    expect(text.indexOf('林夏')).toBeLessThan(text.indexOf('团队'))
    expect(text.indexOf('团队')).toBeLessThan(text.indexOf('知是开发组'))
    expect(screen.getAllByLabelText('团队操作')).toHaveLength(1)
  })

  it('团队展开后有「额度」一页', async () => {
    getMyTeams.mockResolvedValue({ data: { teams: [team('crew', '知是开发组', 'MEMBER')] } })
    await mount('/teams/crew')
    await waitFor(() => expect(hrefs()).toContain('/teams/crew/credits'))
  })

  it('空间点了就进那个空间', async () => {
    await mount('/inbox')
    await screen.findByText('数据分析课')
    expect(hrefs()).toContain('/spaces/3/tasks')
  })
})
