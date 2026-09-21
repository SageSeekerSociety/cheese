/** 成员页：名册是「有谁」，右边那两个按钮是「找他」和「管他」。
 *
 * 这一份钉的是那些没有任何提示、错了也照样渲染的东西：管理动作对谁出现（后端
 * 只让 owner / lead 写，前端给不该有的人一个按钮 = 一个必定失败的按钮），所有者
 * 和自己那两行不能被降职或踢掉，以及私聊落在的地址和侧栏那条私聊行是同一个。
 */
import type { Component } from 'vue'
import type { ProjectMemberRow } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const inviteProjectMember = vi.fn()
const listProjectInvitations = vi.fn()
const revokeInvitation = vi.fn()
const updateProjectMemberRole = vi.fn()
const removeProjectMember = vi.fn()
const leaveProject = vi.fn()
const listProjectAgents = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    inviteProjectMember: (...a: unknown[]) => inviteProjectMember(...a),
    listProjectInvitations: (...a: unknown[]) => listProjectInvitations(...a),
    revokeInvitation: (...a: unknown[]) => revokeInvitation(...a),
    updateProjectMemberRole: (...a: unknown[]) => updateProjectMemberRole(...a),
    removeProjectMember: (...a: unknown[]) => removeProjectMember(...a),
    leaveProject: (...a: unknown[]) => leaveProject(...a),
    listProjectAgents: (...a: unknown[]) => listProjectAgents(...a),
  }
})

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push, replace: vi.fn() }), useRoute: () => ({ query: {} }) }))

// 邀请那一步要按 uid 查人，走的是 1.0 那层的 /users/{id}。整个 network 模块拉进来
// 会连带把真的 router 建起来（它在模块作用域里 createRouter），和上面这个 mock 打架。
const getUserInfo = vi.fn()
vi.mock('@/network/api/users', () => ({ UserApi: { getUserInfo: (...a: unknown[]) => getUserInfo(...a) } }))

let meHandle = 'alice'
vi.mock('@/me', () => ({ myHandle: () => meHandle }))

const refreshMembers = vi.fn()
const refreshProjects = vi.fn()
let members: ProjectMemberRow[] = []
let privateUnreadMap: Record<string, number> = {}
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    members,
    privateUnreadMap,
    projects: [{ id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice' }],
    refreshMembers,
    refreshProjects,
  }),
}))

import ProjectMembersView from './ProjectMembersView.vue'

const Page = ProjectMembersView as unknown as Component

function member(over: Partial<ProjectMemberRow> = {}): ProjectMemberRow {
  return { user_handle: 'ligan', role: 'member', name: '李干', ...over }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  // 菜单和对话框是真的 overlay，Vuetify 的定位策略直接读这几个全局量；jsdom 没有。
  // 同 ResizeObserver 一样是环境缺件，不补的话第一个点击用例炸掉。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

beforeEach(() => {
  push.mockReset()
  refreshMembers.mockReset()
  inviteProjectMember.mockReset().mockResolvedValue({})
  listProjectInvitations.mockReset().mockResolvedValue({ data: [], total: 0 })
  revokeInvitation.mockReset().mockResolvedValue({})
  updateProjectMemberRole.mockReset().mockResolvedValue({})
  removeProjectMember.mockReset().mockResolvedValue({ deleted: true })
  leaveProject.mockReset().mockResolvedValue({ deleted: true })
  refreshProjects.mockReset().mockResolvedValue(undefined)
  listProjectAgents.mockReset().mockResolvedValue({
    data: [
      { handle: 'cheese', display_name: '芝士', is_default: true, is_active: true },
      { handle: 'reviewer', display_name: '评审', is_default: false, is_active: true },
    ],
  })
  privateUnreadMap = {}
  getUserInfo.mockReset().mockResolvedValue({ data: { user: { id: 1024, username: 'zhangheng', nickname: '张衡' } } })
  meHandle = 'alice'
  members = [
    member({ user_handle: 'alice', name: '爱丽丝', role: 'lead' }),
    member({ user_handle: 'bobby', name: '波比', role: 'lead' }),
    member({ user_handle: 'ligan', name: '李干', role: 'member' }),
    member({ user_handle: 'mentor1', name: '老师', role: 'mentor' }),
    member({ user_handle: 'cheese-x', name: '芝士', role: 'member', agent: true }),
  ]
})

function mount() {
  return render(Page, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function rowFor(container: Element, handle: string): Element {
  const row = Array.from(container.querySelectorAll('.member-row')).find((el) =>
    (el.textContent ?? '').includes(`@${handle}`)
  )
  if (!row) throw new Error(`名册上没有 @${handle}`)
  return row
}

// AI 队友那一段的行。它们不是名册行：来源是队友列表接口，不是 store.members。
function agentRows(container: Element): Element[] {
  return Array.from(container.querySelectorAll('.agent-row'))
}

function groupTitles(container: Element): string[] {
  return Array.from(container.querySelectorAll('.t-eyebrow')).map((n) => (n.textContent ?? '').trim())
}

describe('成员页', () => {
  it('人按角色分组，AI 队友单独一段——它没有可升降的角色', async () => {
    const { container } = mount()
    // 队友那一段要等它自己那个接口回来（名册里没有它们）。
    await waitFor(() => expect(groupTitles(container)).toContain('AI 队友 · 2'))
    const titles = groupTitles(container)
    expect(titles).toContain('组长 · 2')
    expect(titles).toContain('导师 · 1')
    expect(titles).toContain('成员 · 1')
    // 芝士不能出现在「成员 · N」那一段里，否则它会带上一个改角色的菜单。
    expect(rowFor(container, 'ligan')).toBeTruthy()
    expect(() => rowFor(container, 'cheese-x')).toThrow()
  })

  it('点私聊落在侧栏那条私聊行的同一个地址上', async () => {
    const { container } = mount()
    await fireEvent.click(rowFor(container, 'ligan').querySelector('[aria-label="私聊"]') as Element)
    expect(push).toHaveBeenCalledWith({
      name: 'workspace-dm',
      params: { projectId: 'p1', peer: 'ligan' },
    })
  })

  it('点一行打开这个人的主页', async () => {
    const { container } = mount()
    await fireEvent.click(rowFor(container, 'ligan').querySelector('.pa-3') as Element)
    expect(push).toHaveBeenCalledWith({ name: 'member', params: { projectId: 'p1', handle: 'ligan' } })
  })

  it('普通成员看不到邀请，也看不到任何一行的管理菜单', () => {
    meHandle = 'ligan'
    const { container, queryByText } = mount()
    expect(queryByText('邀请成员')).toBeNull()
    expect(container.querySelectorAll('[aria-label="管理成员"]').length).toBe(0)
  })

  it('组长能管别人，但管不了项目所有者，也管不了自己', () => {
    meHandle = 'bobby' // lead，不是 owner
    const { container } = mount()
    expect(rowFor(container, 'ligan').querySelector('[aria-label="管理成员"]')).toBeTruthy()
    expect(rowFor(container, 'alice').querySelector('[aria-label="管理成员"]')).toBeNull()
    expect(rowFor(container, 'bobby').querySelector('[aria-label="管理成员"]')).toBeNull()
  })

  it('升职调接口并让名册重新拉一遍——侧栏和 @ 菜单读的是同一份', async () => {
    const { container } = mount()
    await fireEvent.click(rowFor(container, 'ligan').querySelector('[aria-label="管理成员"]') as Element)
    await fireEvent.click(await screen.findByText('设为组长'))
    await waitFor(() => expect(updateProjectMemberRole).toHaveBeenCalledWith('p1', 'ligan', 'lead'))
    await waitFor(() => expect(refreshMembers).toHaveBeenCalled())
  })

  it('移出要先确认——菜单里点一下不会直接把人踢了', async () => {
    const { container } = mount()
    await fireEvent.click(rowFor(container, 'ligan').querySelector('[aria-label="管理成员"]') as Element)
    await fireEvent.click(await screen.findByText('移出项目'))
    expect(removeProjectMember).not.toHaveBeenCalled()
    await fireEvent.click(await screen.findByRole('button', { name: '移出' }))
    await waitFor(() => expect(removeProjectMember).toHaveBeenCalledWith('p1', 'ligan'))
  })

  // 退出项目是**自己走**那条路（后端 `DELETE /projects/{id}/membership`），不是名册
  // 上某一行的管理动作——所以它在右上角，不在任何一行里，而且非所有者都看得见。
  // 「来自小队」的人照样看得见：他得到的那句「请在小队里退出」正是他要的下一步。
  it('退出项目要先确认——点一下不会直接退出去', async () => {
    meHandle = 'ligan'
    const { getByText } = mount()
    await fireEvent.click(getByText('退出项目'))
    expect(leaveProject).not.toHaveBeenCalled()
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    await waitFor(() => expect(leaveProject).toHaveBeenCalledWith('p1'))
  })

  it('退出去之后刷新名册和项目列表，并离开这一页', async () => {
    meHandle = 'ligan'
    const { getByText } = mount()
    await fireEvent.click(getByText('退出项目'))
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    // 不刷新就走的话，退到的那一页会拿着旧数据把我送回这个项目。
    await waitFor(() => expect(refreshMembers).toHaveBeenCalled())
    expect(refreshProjects).toHaveBeenCalled()
    await waitFor(() => expect(push).toHaveBeenCalledWith({ name: 'HomeSpaces' }))
  })

  // 退出请求成功的那一刻，人**已经不在项目里了**。刷新是锦上添花（让别的页面不拿着
  // 旧数据把我送回来），它失败不该把一件已经做成的事报成失败：页面上挂一句「退出失
  // 败」，而人其实退掉了，再点一次只会拿到 409。所以刷新失败也照样走。
  it('刷新失败也照样离开——退出已经成功了', async () => {
    meHandle = 'ligan'
    refreshMembers.mockRejectedValue(new Error('boom'))
    refreshProjects.mockRejectedValue(new Error('boom'))
    const { getByText, queryByText } = mount()
    await fireEvent.click(getByText('退出项目'))
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    await waitFor(() => expect(push).toHaveBeenCalledWith({ name: 'HomeSpaces' }))
    expect(queryByText(/退出失败/)).toBeNull()
  })

  it('退不掉时把后端那句理由说出来，人留在原地', async () => {
    meHandle = 'ligan'
    leaveProject.mockRejectedValue(new Error('你对这个项目的访问来自所属小队，退出项目要在小队里操作'))
    const { getByText } = mount()
    await fireEvent.click(getByText('退出项目'))
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    expect(await screen.findByText(/退出项目要在小队里操作/)).toBeTruthy()
    expect(push).not.toHaveBeenCalled()
  })

  it('所有者看不到退出项目——后端会拒他，按钮不该先给一个必定失败的动作', () => {
    const { queryByText } = mount()
    expect(queryByText('退出项目')).toBeNull()
  })

  // 邀请按 uid，不按 handle：uid 抄得准（就在个人主页地址里），而 handle 打错一个
  // 字母的后果是「查无此人」还是「加错了人」全看运气。所以这一组守的是「按下按钮
  // 之前，人已经看见自己要加的是谁」。
  it('填 uid → 先查出这个人是谁，再把他的 handle 交给后端', async () => {
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请成员'))
    await fireEvent.update(await screen.findByLabelText('uid'), '1024')
    // 查到的人要显示出来给人确认
    expect(await screen.findByText('张衡')).toBeTruthy()
    expect(getUserInfo).toHaveBeenCalledWith(1024)
    await fireEvent.click(await screen.findByRole('button', { name: '邀请' }))
    // 后端认的是 handle，uid 只是人这边好抄的那个号；而且发出去的是**邀请**，
    // 不是直接把人放上名册——那正是这个功能的意义。
    await waitFor(() => expect(inviteProjectMember).toHaveBeenCalledWith('p1', 'zhangheng', 'member'))
  })

  it('查无此人 → 说出来，并且按钮按不下去', async () => {
    getUserInfo.mockRejectedValue(new Error('404'))
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请成员'))
    await fireEvent.update(await screen.findByLabelText('uid'), '999999')
    expect(await screen.findByText(/找不到 uid 999999/)).toBeTruthy()
    const btn = await screen.findByRole('button', { name: '邀请' })
    expect(btn.hasAttribute('disabled')).toBe(true)
    await fireEvent.click(btn)
    expect(inviteProjectMember).not.toHaveBeenCalled()
  })

  it('这个人已经在项目里 → 说出来，不让再邀一次', async () => {
    getUserInfo.mockResolvedValue({ data: { user: { id: 7, username: 'ligan', nickname: '李干' } } })
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请成员'))
    await fireEvent.update(await screen.findByLabelText('uid'), '7')
    expect(await screen.findByText('已经在项目里')).toBeTruthy()
    expect((await screen.findByRole('button', { name: '邀请' })).hasAttribute('disabled')).toBe(true)
  })

  it('还没查到人之前按不下去——空邀请是个必定失败的请求', async () => {
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请成员'))
    expect((await screen.findByRole('button', { name: '邀请' })).hasAttribute('disabled')).toBe(true)
    expect(getUserInfo).not.toHaveBeenCalled()
  })
})

// 侧栏那一段私聊撤掉之后，未读只剩两个落点：侧栏「成员」那一行上的总数，和这一页
// 上每个人自己那一颗。这一组守的是第二个——它错了的表现是「知道有人找你，但点进来
// 看不出是谁」，而那正是把这一段搬过来的全部理由。
describe('成员页：私聊未读', () => {
  it('谁有未读，红点就长在谁那颗私聊按钮上', async () => {
    privateUnreadMap = { ligan: 3 }
    const { container } = mount()
    await waitFor(() => expect(rowFor(container, 'ligan').querySelector('.dm-unread')).toBeTruthy())
    expect(rowFor(container, 'ligan').querySelector('.dm-unread')?.textContent?.trim()).toBe('3')
    // 没有未读的人不该也挂一个
    expect(rowFor(container, 'mentor1').querySelector('.dm-unread')).toBeNull()
  })

  // 每个队友一间私聊，所以每个队友一行、一颗按钮、一份未读。合成一行的老写法
  // 会让「谁找你」在有第二个队友的项目里彻底失真。
  it('每个 AI 队友各有一行、一颗私聊按钮和自己的未读', async () => {
    privateUnreadMap = { 'agent:reviewer': 2 }
    const { container, getByText } = mount()
    const rows = await waitFor(() => {
      const found = agentRows(container)
      if (found.length < 2) throw new Error('队友还没渲染出来')
      return found
    })
    expect(getByText('AI 队友 · 2')).toBeTruthy()

    const reviewer = rows.find((r) => (r.textContent ?? '').includes('@reviewer')) as Element
    const cheese = rows.find((r) => (r.textContent ?? '').includes('@cheese')) as Element
    expect(reviewer.querySelector('.dm-unread')?.textContent?.trim()).toBe('2')
    // 未读是那个队友自己的，不会印到另一个队友那一行上
    expect(cheese.querySelector('.dm-unread')).toBeNull()

    await fireEvent.click(reviewer.querySelector('[aria-label="私聊"]') as Element)
    // 地址带 agent: 前缀，和人的 handle 分开——队友的名字是项目自己起的，可以撞。
    expect(push).toHaveBeenCalledWith({ name: 'workspace-dm', params: { projectId: 'p1', peer: 'agent:reviewer' } })
  })

  it('停用的队友不列出来——它在老话题里照常工作，但不该拿出来开新对话', async () => {
    listProjectAgents.mockResolvedValue({
      data: [
        { handle: 'cheese', display_name: '芝士', is_default: true, is_active: true },
        { handle: 'retired', display_name: '退休', is_default: false, is_active: false },
      ],
    })
    const { container } = mount()
    await waitFor(() => expect(agentRows(container).length).toBe(1))
    expect(agentRows(container)[0].textContent).toContain('@cheese')
  })

  it('拿不到队友名单时这一页照样能用，只是没有 AI 队友那一段', async () => {
    listProjectAgents.mockRejectedValue(new Error('boom'))
    const { container } = mount()
    await waitFor(() => expect(rowFor(container, 'ligan')).toBeTruthy())
    expect(agentRows(container).length).toBe(0)
  })
})

// 邀请发出去之后，邀请方看得见自己在等谁。看不见的话，「我到底邀没邀过他」只能靠
// 记性——而重复邀请会被后端拒掉，人却不知道为什么。
describe('成员页：等待接受', () => {
  const pending = {
    id: 'inv-1',
    project_id: 'p1',
    invitee_handle: 'zhangheng',
    inviter_handle: 'alice',
    role: 'lead',
    status: 'pending' as const,
    created_at: '2026-09-08T00:00:00Z',
    responded_at: null,
  }

  it('待答复的邀请单独一段，不混进名册', async () => {
    listProjectInvitations.mockResolvedValue({ data: [pending], total: 1 })
    const { container } = mount()
    await waitFor(() => expect(groupTitles(container)).toContain('等待接受 · 1'))
    // 他还不是成员，所以不能出现在角色分组里
    expect(() => rowFor(container, 'zhangheng')).toThrow()
  })

  it('组长能把发出去的邀请撤回来', async () => {
    listProjectInvitations.mockResolvedValue({ data: [pending], total: 1 })
    const { container } = mount()
    await waitFor(() => expect(groupTitles(container)).toContain('等待接受 · 1'))
    await fireEvent.click(await screen.findByRole('button', { name: '撤回' }))
    await waitFor(() => expect(revokeInvitation).toHaveBeenCalledWith('inv-1'))
  })

  it('普通成员看不到撤回', async () => {
    meHandle = 'ligan'
    listProjectInvitations.mockResolvedValue({ data: [pending], total: 1 })
    const { container } = mount()
    await waitFor(() => expect(groupTitles(container)).toContain('等待接受 · 1'))
    expect(container.textContent).not.toContain('撤回')
  })
})

// 所有者在成员表里没有自己那一行（这个仓里所有者记在 Project.owner_handle 上），
// 名册接口在读的时候把他补进来，带 source: 'owner'。这一组守的是这一行在页面上和
// 别人一样是个正常的人——只是没有可以改的角色。
describe('成员页：所有者那一行', () => {
  const owner = (): ProjectMemberRow => ({
    user_handle: 'alice',
    role: 'lead',
    name: '爱丽丝',
    source: 'owner',
  })

  it('刚建好、还没加过人的项目，页面上是所有者，不是「还没有成员」', () => {
    members = [owner()]
    const { container } = mount()
    expect(container.textContent).toContain('@alice')
    expect(container.textContent).toContain('所有者')
    expect(container.textContent).not.toContain('还没有成员')
  })

  it('他在角色分组里——不是浮在名单外面的一行', () => {
    members = [owner()]
    const { container } = mount()
    expect(groupTitles(container)).toContain('组长 · 1')
  })

  it('他那一行没有管理菜单：角色和移出都动不了成员表里不存在的一行', () => {
    members = [owner(), member()]
    const { container } = mount()
    expect(rowFor(container, 'alice').querySelector('[aria-label="管理成员"]')).toBeNull()
    // 名册里真有他自己那一行时（他也被显式加进过成员表），也只画一行。
    expect(container.querySelectorAll('.member-row').length).toBe(2)
  })
})
