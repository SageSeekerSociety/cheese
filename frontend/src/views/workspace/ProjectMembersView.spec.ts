/** 成员页：一个项目的人按角色分段——所有者、管理员、成员、导师（#3830）。
 *
 * 这一份钉的是那些错了也照样渲染的东西：每个人落在哪一段、谁挂「外部」，只往下管
 * （⋯ 菜单只出现在比我低的人身上，设管理员只有所有者能做），邀请时选的角色交给
 * 后端，以及退出、转让、移到团队、撤回转让、私聊这几颗按钮对谁出现。
 *
 * 我坐在哪、能做什么只看后端在 `GET /projects/{id}` 上给的 `my_role` / `my_permissions`。
 */
import type { Component } from 'vue'
import type { Project, ProjectMemberRow } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const inviteExternalMember = vi.fn()
const lookupUser = vi.fn()
const listProjectInvitations = vi.fn()
const revokeInvitation = vi.fn()
const removeProjectMember = vi.fn()
const setMemberRole = vi.fn()
const cancelTransfer = vi.fn()
const listProjectAgents = vi.fn()
const getProject = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    inviteExternalMember: (...a: unknown[]) => inviteExternalMember(...a),
    lookupUser: (...a: unknown[]) => lookupUser(...a),
    listProjectInvitations: (...a: unknown[]) => listProjectInvitations(...a),
    revokeInvitation: (...a: unknown[]) => revokeInvitation(...a),
    removeProjectMember: (...a: unknown[]) => removeProjectMember(...a),
    setMemberRole: (...a: unknown[]) => setMemberRole(...a),
    cancelTransfer: (...a: unknown[]) => cancelTransfer(...a),
    listProjectAgents: (...a: unknown[]) => listProjectAgents(...a),
    getProject: (...a: unknown[]) => getProject(...a),
  }
})

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push, replace: vi.fn() }), useRoute: () => ({ query: {} }) }))

let meHandle = 'alice'
vi.mock('@/me', () => ({ myHandle: () => meHandle }))

type Seat = 'owner' | 'admin' | 'member' | 'mentor'
type Row = ProjectMemberRow & { role?: Seat; role_from_team?: boolean }

const refreshMembers = vi.fn()
let members: Row[] = []
let privateUnreadMap: Record<string, number> = {}
// p1 挂在团队 zhishi 下；自己名下的项目，团队地址就是所有者的用户名。
let projectRow: { id: string; name: string; created_at: string; owner_handle: string; team_handle: string }
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    // 成员页总是开在 store 正开着的那个项目上。
    projectId: 'p1',
    members,
    privateUnreadMap,
    projects: [projectRow],
    refreshMembers,
    refreshProjects: vi.fn(),
  }),
}))

import ProjectMembersView from './ProjectMembersView.vue'

import { ApiError } from '@/api'
import i18n, { setLocale } from '@/i18n'

const Page = ProjectMembersView as unknown as Component

let vuetify: ReturnType<typeof createVuetify>

/** 我是谁、在这个项目里坐哪、能做什么、有没有一笔等着对方接受的转让。 */
let seat: Pick<Project, 'my_role' | 'my_permissions' | 'pending_transfer'> = {}
function as(handle: string, my_role: Seat, my_permissions: string[], extra: Partial<Project> = {}) {
  meHandle = handle
  seat = { my_role, my_permissions, ...extra }
}
const MANAGER = ['manage_members', 'change_roles', 'speak', 'comment']
const PLAIN = ['speak', 'comment']

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
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
  // The invite dialog's overlay reads it while it animates out, after the test
  // has finished; happy-dom does not define it.
  if (!globalThis.devicePixelRatio) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  push.mockReset()
  refreshMembers.mockReset().mockResolvedValue(undefined)
  inviteExternalMember.mockReset().mockResolvedValue({})
  lookupUser.mockReset()
  listProjectInvitations.mockReset().mockResolvedValue({ data: [], total: 0 })
  revokeInvitation.mockReset().mockResolvedValue({})
  removeProjectMember.mockReset().mockResolvedValue({ deleted: true })
  setMemberRole.mockReset().mockResolvedValue({ role: 'member' })
  cancelTransfer.mockReset().mockResolvedValue({})
  getProject.mockReset().mockImplementation(async (id: string) => ({ ...projectRow, id, ...seat }))
  listProjectAgents.mockReset().mockResolvedValue({
    data: [{ handle: 'cheese', display_name: '芝士', is_default: true, is_active: true }],
  })
  privateUnreadMap = {}
  projectRow = { id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', team_handle: 'zhishi' }
  as('alice', 'owner', [...MANAGER, 'edit_settings'])
  members = [
    { user_handle: 'alice', name: '爱丽丝', source: 'owner', role: 'owner' },
    {
      user_handle: 'boss',
      name: '团长',
      source: 'team',
      team_handle: 'zhishi',
      team_name: '知是小队',
      role: 'admin',
      role_from_team: true,
    },
    { user_handle: 'dora', name: '朵拉', source: 'team', team_handle: 'zhishi', team_name: '知是小队', role: 'admin' },
    {
      user_handle: 'ligan',
      name: '李干',
      source: 'team',
      team_handle: 'zhishi',
      team_name: '知是小队',
      role: 'member',
    },
    { user_handle: 'mentor1', name: '老师', source: 'external', role: 'mentor' },
    { user_handle: 'cheese-x', name: '芝士', source: 'agent', agent: true },
  ]
})

function mount() {
  return render(Page, { props: { projectId: 'p1' }, global: { plugins: [vuetify, i18n] } })
}

/** 项目那一份读回来之后才知道我坐哪：断言「没有」之前先等它到。 */
async function settled() {
  await waitFor(() => expect(getProject).toHaveBeenCalledWith('p1'))
  await new Promise((resolve) => setTimeout(resolve))
}

const MENU = '[aria-label="管理成员"]'

/** 打开某一行的 ⋯ 菜单，返回菜单里各项的文字。 */
async function openMenu(container: Element, handle: string): Promise<string[]> {
  await waitFor(() => expect(rowFor(container, handle).querySelector(MENU)).toBeTruthy())
  await fireEvent.click(rowFor(container, handle).querySelector(MENU) as Element)
  await screen.findByText('移出项目')
  return Array.from(document.body.querySelectorAll('.v-overlay .v-list-item'), (el) => el.textContent?.trim() ?? '')
}

function section(container: Element, key: string): Element | null {
  return container.querySelector(`[data-section="${key}"]`)
}

// A row is found by the handle printed on it, as is: a bare handle, no `@`.
function showsHandle(el: Element, handle: string): boolean {
  return Array.from(el.querySelectorAll('*')).some((n) => n.children.length === 0 && n.textContent?.trim() === handle)
}

function rowFor(container: Element, handle: string): Element {
  const row = Array.from(container.querySelectorAll('.member-row')).find((el) => showsHandle(el, handle))
  if (!row) throw new Error(`名册上没有 ${handle}`)
  return row
}

describe('成员页：按角色分段', () => {
  it('所有者、管理员、成员、导师各一段，AI 队友不混进人里', () => {
    const { container } = mount()
    expect(showsHandle(section(container, 'owner')!, 'alice')).toBe(true)
    expect(showsHandle(section(container, 'admin')!, 'boss')).toBe(true)
    expect(showsHandle(section(container, 'admin')!, 'dora')).toBe(true)
    expect(showsHandle(section(container, 'member')!, 'ligan')).toBe(true)
    expect(showsHandle(section(container, 'mentor')!, 'mentor1')).toBe(true)
    expect(showsHandle(section(container, 'member')!, 'mentor1')).toBe(false)
    expect(container.textContent).not.toContain('@alice')
    expect(() => rowFor(container, 'cheese-x')).toThrow()
  })

  it('团队给的管理员身份写明来自团队：这一条只能在团队那边改', () => {
    const { container } = mount()
    expect(rowFor(container, 'boss').textContent).toContain('团队管理员')
    expect(rowFor(container, 'dora').textContent).not.toContain('团队管理员')
  })

  it('只有外部成员挂「外部」', () => {
    const { container } = mount()
    expect(rowFor(container, 'mentor1').textContent).toContain('外部')
    expect(rowFor(container, 'ligan').textContent).not.toContain('外部')
    expect(rowFor(container, 'alice').textContent).not.toContain('外部')
  })

  it('团队成员指回他所在的团队，用团队的名字', () => {
    const { container } = mount()
    const link = rowFor(container, 'ligan').querySelector('.member-team-link')!
    expect(link.textContent?.trim()).toBe('来自团队 知是小队')
  })

  it('点私聊落在侧栏那条私聊行的同一个地址上', async () => {
    const { container } = mount()
    await fireEvent.click(rowFor(container, 'ligan').querySelector('[aria-label="私聊"]') as Element)
    expect(push).toHaveBeenCalledWith({ name: 'workspace-dm', params: { projectId: 'p1', peer: 'ligan' } })
  })
})

describe('成员页：只往下管', () => {
  it('所有者对除自己以外的每个人都有管理菜单', async () => {
    const { container } = mount()
    for (const handle of ['dora', 'ligan', 'mentor1']) {
      await waitFor(() => expect(rowFor(container, handle).querySelector(MENU), handle).toBeTruthy())
    }
    expect(rowFor(container, 'alice').querySelector(MENU)).toBeNull()
  })

  it('所有者能把成员设为管理员或导师，也能移出', async () => {
    const { container } = mount()
    const items = await openMenu(container, 'ligan')
    expect(items).toEqual(expect.arrayContaining(['设为管理员', '设为导师', '移出项目']))
    expect(items).not.toContain('设为成员')
  })

  it('导师只能被设回成员，不能一步升成管理员', async () => {
    const { container } = mount()
    const items = await openMenu(container, 'mentor1')
    expect(items).toContain('设为成员')
    expect(items).not.toContain('设为管理员')
    expect(items).not.toContain('设为导师')
  })

  it('改角色交给后端，然后刷新名册', async () => {
    const { container } = mount()
    await openMenu(container, 'ligan')
    await fireEvent.click(await screen.findByText('设为导师'))
    await waitFor(() => expect(setMemberRole).toHaveBeenCalledWith('p1', 'ligan', 'mentor'))
    await waitFor(() => expect(refreshMembers).toHaveBeenCalled())
  })

  it('改角色被拒时把理由说出来', async () => {
    setMemberRole.mockRejectedValue(new Error('只有所有者能设管理员'))
    const { container, findByText } = mount()
    await openMenu(container, 'ligan')
    await fireEvent.click(await screen.findByText('设为管理员'))
    expect(await findByText('只有所有者能设管理员')).toBeTruthy()
  })

  it('管理员只管成员和导师：所有者、别的管理员、自己都没有菜单，也设不了管理员', async () => {
    as('dora', 'admin', MANAGER)
    const { container } = mount()
    await waitFor(() => expect(rowFor(container, 'ligan').querySelector(MENU)).toBeTruthy())
    expect(rowFor(container, 'mentor1').querySelector(MENU)).toBeTruthy()
    expect(rowFor(container, 'alice').querySelector(MENU)).toBeNull()
    expect(rowFor(container, 'boss').querySelector(MENU)).toBeNull()
    expect(rowFor(container, 'dora').querySelector(MENU)).toBeNull()

    const items = await openMenu(container, 'ligan')
    expect(items).toContain('设为导师')
    expect(items).not.toContain('设为管理员')
  })

  it('成员谁也管不了：没有管理菜单，也看不到邀请', async () => {
    as('ligan', 'member', PLAIN)
    const { container, queryByText } = mount()
    await settled()
    expect(container.querySelectorAll(MENU).length).toBe(0)
    expect(queryByText('邀请')).toBeNull()
  })

  it('移出要先确认，确认后调接口并刷新名册', async () => {
    const { container } = mount()
    await openMenu(container, 'mentor1')
    await fireEvent.click(await screen.findByText('移出项目'))
    expect(removeProjectMember).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    await fireEvent.click(within(dialog).getByRole('button', { name: '移出' }))
    await waitFor(() => expect(removeProjectMember).toHaveBeenCalledWith('p1', 'mentor1'))
    expect(refreshMembers).toHaveBeenCalled()
  })
})

describe('成员页：退出、转让与移到团队', () => {
  it('导师能退出项目，不能转让', async () => {
    as('mentor1', 'mentor', PLAIN)
    const { findByText, queryByText } = mount()
    expect(await findByText('退出项目')).toBeTruthy()
    expect(queryByText('转让项目')).toBeNull()
  })

  it('管理员也只能退出，不能转让', async () => {
    as('dora', 'admin', MANAGER)
    const { findByText, queryByText } = mount()
    expect(await findByText('退出项目')).toBeTruthy()
    expect(queryByText('转让项目')).toBeNull()
  })

  it('团队成员也能退出项目——他退的是这个项目，不是团队', async () => {
    as('ligan', 'member', PLAIN)
    const { findByText } = mount()
    const leave = await findByText('退出项目')
    await fireEvent.click(leave)
    // 确认框必须说清楚退的是哪一样：只说「退出」，在团队里也读得通。
    expect(await screen.findByText(/退出的是这个项目，不是团队/)).toBeTruthy()
  })

  it('不在名册上的人看不到退出项目——他已经不在这个项目里了', async () => {
    as('stranger', 'member', PLAIN)
    const { queryByText } = mount()
    await settled()
    expect(queryByText('退出项目')).toBeNull()
  })

  it('所有者看到的是转让项目，不是退出', async () => {
    const { findByText, queryByText } = mount()
    expect(await findByText('转让项目')).toBeTruthy()
    expect(queryByText('退出项目')).toBeNull()
  })

  it('团队项目的所有者没有「移到团队」：它已经在团队里', async () => {
    const { findByText, queryByText } = mount()
    await findByText('转让项目')
    expect(queryByText('移到团队')).toBeNull()
  })

  it('个人项目的所有者能把它移到团队', async () => {
    projectRow = { ...projectRow, team_handle: 'alice' }
    const { findByText } = mount()
    expect(await findByText('移到团队')).toBeTruthy()
  })

  it('个人项目里的管理员不能把它移到团队', async () => {
    projectRow = { ...projectRow, team_handle: 'alice' }
    as('dora', 'admin', MANAGER)
    const { findByText, queryByText } = mount()
    await findByText('退出项目')
    expect(queryByText('移到团队')).toBeNull()
  })

  it('每个人都能去看角色和权限', async () => {
    as('mentor1', 'mentor', PLAIN)
    const { findByText } = mount()
    await fireEvent.click(await findByText('角色和权限'))
    expect(push).toHaveBeenCalledWith({ name: 'project-settings', params: { projectId: 'p1', section: 'roles' } })
  })

  it('有一笔转让在等对方接受：说出在等谁，可以撤回', async () => {
    as('alice', 'owner', MANAGER, { pending_transfer: { id: 't1', to_handle: 'ligan' } })
    const { findByText } = mount()
    expect(await findByText('等待 李干 接受转让')).toBeTruthy()
    await fireEvent.click(await findByText('撤回转让'))
    await waitFor(() => expect(cancelTransfer).toHaveBeenCalledWith('p1'))
  })

  it('没有在等的转让时不提转让', async () => {
    const { findByText, queryByText } = mount()
    await findByText('转让项目')
    expect(queryByText('撤回转让')).toBeNull()
    expect(queryByText(/接受转让/)).toBeNull()
  })
})

describe('成员页：邀请', () => {
  /** 打开邀请框，按用户名或邮箱查到这个人。 */
  async function lookUp(query: string) {
    await fireEvent.click(await screen.findByText('邀请'))
    const dialog = await screen.findByRole('dialog')
    await fireEvent.update(within(dialog).getByLabelText('用户名或邮箱'), query)
    return dialog
  }

  it('按用户名或邮箱查到人，摆出来确认，默认以成员身份邀请', async () => {
    lookupUser.mockResolvedValue({ handle: 'zhangheng', name: '张衡', avatar_id: null })
    mount()
    const dialog = await lookUp('zh@example.com')
    await waitFor(() => expect(lookupUser).toHaveBeenCalledWith('zh@example.com'))
    expect(await within(dialog).findByText('张衡')).toBeTruthy()
    await fireEvent.click(within(dialog).getByRole('button', { name: '发送邀请' }))
    await waitFor(() => expect(inviteExternalMember).toHaveBeenCalledWith('p1', 'zhangheng', 'member'))
  })

  it('选了导师就以导师身份邀请', async () => {
    lookupUser.mockResolvedValue({ handle: 'zhangheng', name: '张衡', avatar_id: null })
    mount()
    const dialog = await lookUp('zhangheng')
    await fireEvent.click(within(dialog).getByRole('radio', { name: '导师' }))
    await within(dialog).findByText('张衡')
    await fireEvent.click(within(dialog).getByRole('button', { name: '发送邀请' }))
    await waitFor(() => expect(inviteExternalMember).toHaveBeenCalledWith('p1', 'zhangheng', 'mentor'))
  })

  it('查无此人 → 说出来，并且按钮按不下去', async () => {
    lookupUser.mockRejectedValue(new ApiError(404, 'not found'))
    mount()
    const dialog = await lookUp('nobody')
    expect(await within(dialog).findByText('找不到这个用户名或邮箱')).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: '发送邀请' }).hasAttribute('disabled')).toBe(true)
  })

  it('已经在项目里的人不让再邀一次', async () => {
    lookupUser.mockResolvedValue({ handle: 'ligan', name: '李干', avatar_id: null })
    mount()
    const dialog = await lookUp('ligan')
    expect(await within(dialog).findByText('已经在项目里')).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: '发送邀请' }).hasAttribute('disabled')).toBe(true)
  })

  it('后端拒绝（比如对方已在团队里）时把理由写在输入框下面', async () => {
    lookupUser.mockResolvedValue({ handle: 'zhangheng', name: '张衡', avatar_id: null })
    inviteExternalMember.mockRejectedValue(new Error('这个人已经在团队里'))
    mount()
    const dialog = await lookUp('zhangheng')
    await within(dialog).findByText('张衡')
    await fireEvent.click(within(dialog).getByRole('button', { name: '发送邀请' }))
    expect(await within(dialog).findByText('这个人已经在团队里')).toBeTruthy()
  })

  it('待答复的邀请单独一段，挂「外部」，管得了的人能撤回', async () => {
    listProjectInvitations.mockResolvedValue({
      data: [
        {
          id: 'inv1',
          project_id: 'p1',
          invitee_handle: 'zhangheng',
          inviter_handle: 'alice',
          status: 'pending',
          created_at: '',
        },
      ],
      total: 1,
    })
    const { container, findByText } = mount()
    await findByText('zhangheng')
    expect(section(container, 'pending')?.textContent).toContain('外部')
    await fireEvent.click(await findByText('撤回'))
    await waitFor(() => expect(revokeInvitation).toHaveBeenCalledWith('inv1'))
  })

  it('待答复的邀请写被邀请人的名字，handle 不带 @ 跟在下面', async () => {
    listProjectInvitations.mockResolvedValue({
      data: [
        {
          id: 'inv2',
          project_id: 'p1',
          invitee_handle: 'zhangheng',
          invitee_name: '张衡',
          invitee_avatar_id: 42,
          inviter_handle: 'alice',
          status: 'pending',
          created_at: '',
        },
      ],
      total: 1,
    })
    const { container, findByText } = mount()
    await findByText('张衡')
    const pending = section(container, 'pending')!
    expect(showsHandle(pending, 'zhangheng')).toBe(true)
    expect(pending.textContent).not.toContain('@zhangheng')
    await waitFor(() => expect(pending.innerHTML).toContain('/avatars/42'))
  })

  it('设了个人备注的队友显示「备注名 (原昵称)」，且按备注名或原名都能搜到', async () => {
    members = [
      { user_handle: 'alice', name: '爱丽丝', source: 'owner', role: 'owner' },
      { user_handle: 'ligan', name: '李干', remark: '前端组件-李工', source: 'team', role: 'member' },
      ...Array.from({ length: 8 }, (_, i) => ({
        user_handle: `m${i}`,
        name: `成员${i}`,
        source: 'team' as const,
        role: 'member' as const,
      })),
    ]
    const { container, findByText } = mount()
    await findByText('前端组件-李工')
    expect(container.textContent).toContain('(李干)')

    const search = container.querySelector('.v-text-field input') as HTMLInputElement
    expect(search).toBeTruthy()
    await fireEvent.update(search, '李干')
    expect(container.textContent).toContain('前端组件-李工')
    expect(container.textContent).not.toContain('成员0')

    await fireEvent.update(search, '前端组件')
    expect(container.textContent).toContain('前端组件-李工')
  })

  it('名册已到货时立即放开显示闸并展示成员与队友，不等 /agents 和 /invitations 返回', () => {
    listProjectAgents.mockReturnValue(new Promise(() => {}))
    listProjectInvitations.mockReturnValue(new Promise(() => {}))
    const { container } = mount()
    expect(container.querySelector('.reveal-gate--waiting')).toBeNull()
    expect(rowFor(container, 'alice')).toBeTruthy()
    expect(container.querySelector('.agent-row')).toBeTruthy()
  })
})
