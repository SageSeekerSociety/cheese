/** 成员页：一个项目的人按来路分三段——所有者、团队成员、外部成员。
 *
 * 这一份钉的是那些错了也照样渲染的东西：谁挂「外部」、谁能被移出（只有外部成员，
 * 而且只有管得了的人看得到），邀请外部成员要先按用户名或邮箱精确查到人再发，以及
 * 退出、转让、私聊这几颗按钮对谁出现。
 */
import type { Component } from 'vue'
import type { ProjectMemberRow } from '@/cx_types'

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
const listProjectAgents = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    inviteExternalMember: (...a: unknown[]) => inviteExternalMember(...a),
    lookupUser: (...a: unknown[]) => lookupUser(...a),
    listProjectInvitations: (...a: unknown[]) => listProjectInvitations(...a),
    revokeInvitation: (...a: unknown[]) => revokeInvitation(...a),
    removeProjectMember: (...a: unknown[]) => removeProjectMember(...a),
    listProjectAgents: (...a: unknown[]) => listProjectAgents(...a),
  }
})

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push, replace: vi.fn() }), useRoute: () => ({ query: {} }) }))

let meHandle = 'alice'
vi.mock('@/me', () => ({ myHandle: () => meHandle }))

const refreshMembers = vi.fn()
let members: ProjectMemberRow[] = []
let privateUnreadMap: Record<string, number> = {}
let projects: {
  id: string
  name: string
  created_at: string
  owner_handle?: string | null
  can_manage_members?: boolean
}[] = []
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    members,
    privateUnreadMap,
    projects,
    refreshMembers,
    refreshProjects: vi.fn(),
  }),
}))

import ProjectMembersView from './ProjectMembersView.vue'

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'

const Page = ProjectMembersView as unknown as Component

let vuetify: ReturnType<typeof createVuetify>

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
  listProjectAgents.mockReset().mockResolvedValue({
    data: [{ handle: 'cheese', display_name: '芝士', is_default: true, is_active: true }],
  })
  privateUnreadMap = {}
  meHandle = 'alice'
  projects = [{ id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', can_manage_members: true }]
  members = [
    { user_handle: 'alice', name: '爱丽丝', source: 'owner' },
    { user_handle: 'ligan', name: '李干', source: 'team', team_handle: 'zhishi' },
    { user_handle: 'mentor1', name: '老师', source: 'external' },
    { user_handle: 'cheese-x', name: '芝士', source: 'agent', agent: true },
  ]
})

function mount() {
  return render(Page, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function section(container: Element, key: string): Element | null {
  return container.querySelector(`[data-section="${key}"]`)
}

function rowFor(container: Element, handle: string): Element {
  const row = Array.from(container.querySelectorAll('.member-row')).find((el) =>
    (el.textContent ?? '').includes(`@${handle}`)
  )
  if (!row) throw new Error(`名册上没有 @${handle}`)
  return row
}

describe('成员页：按来路分段', () => {
  it('所有者、团队成员、外部成员各一段，AI 队友不混进人里', () => {
    const { container } = mount()
    expect(section(container, 'owner')?.textContent).toContain('@alice')
    expect(section(container, 'team')?.textContent).toContain('@ligan')
    expect(section(container, 'external')?.textContent).toContain('@mentor1')
    expect(() => rowFor(container, 'cheese-x')).toThrow()
  })

  it('只有外部成员挂「外部」', () => {
    const { container } = mount()
    expect(rowFor(container, 'mentor1').textContent).toContain('外部')
    expect(rowFor(container, 'ligan').textContent).not.toContain('外部')
    expect(rowFor(container, 'alice').textContent).not.toContain('外部')
  })

  it('团队成员指回他所在的团队', () => {
    const { container } = mount()
    expect(rowFor(container, 'ligan').textContent).toContain('来自团队 @zhishi')
  })

  it('点私聊落在侧栏那条私聊行的同一个地址上', async () => {
    const { container } = mount()
    await fireEvent.click(rowFor(container, 'ligan').querySelector('[aria-label="私聊"]') as Element)
    expect(push).toHaveBeenCalledWith({ name: 'workspace-dm', params: { projectId: 'p1', peer: 'ligan' } })
  })
})

describe('成员页：谁能管外部成员', () => {
  it('管得了的人只在外部成员那一行看到管理菜单', () => {
    const { container } = mount()
    expect(rowFor(container, 'mentor1').querySelector('[aria-label="管理成员"]')).toBeTruthy()
    expect(rowFor(container, 'ligan').querySelector('[aria-label="管理成员"]')).toBeNull()
    expect(rowFor(container, 'alice').querySelector('[aria-label="管理成员"]')).toBeNull()
  })

  it('管不了的人看不到邀请，也看不到任何管理菜单', () => {
    meHandle = 'ligan'
    projects = [{ id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', can_manage_members: false }]
    const { container, queryByText } = mount()
    expect(queryByText('邀请外部成员')).toBeNull()
    expect(container.querySelectorAll('[aria-label="管理成员"]').length).toBe(0)
  })

  it('移出外部成员要先确认，确认后调接口并刷新名册', async () => {
    const { container } = mount()
    await fireEvent.click(rowFor(container, 'mentor1').querySelector('[aria-label="管理成员"]') as Element)
    await fireEvent.click(await screen.findByText('移出项目'))
    expect(removeProjectMember).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    await fireEvent.click(within(dialog).getByRole('button', { name: '移出' }))
    await waitFor(() => expect(removeProjectMember).toHaveBeenCalledWith('p1', 'mentor1'))
    expect(refreshMembers).toHaveBeenCalled()
  })
})

describe('成员页：退出与转让', () => {
  it('外部成员能退出项目，团队成员不能——他在这里是因为在团队里', () => {
    meHandle = 'mentor1'
    projects = [{ id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', can_manage_members: false }]
    expect(mount().queryByText('退出项目')).toBeTruthy()
  })

  it('团队成员看不到退出项目', () => {
    meHandle = 'ligan'
    projects = [{ id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', can_manage_members: false }]
    expect(mount().queryByText('退出项目')).toBeNull()
  })

  it('所有者看到的是转让项目，不是退出', () => {
    const { queryByText } = mount()
    expect(queryByText('退出项目')).toBeNull()
    expect(queryByText('转让项目')).toBeTruthy()
  })
})

describe('成员页：邀请外部成员', () => {
  it('按用户名或邮箱查到人，摆出来确认，再把他的 handle 交给后端', async () => {
    lookupUser.mockResolvedValue({ handle: 'zhangheng', name: '张衡', avatar_id: null })
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请外部成员'))
    const dialog = await screen.findByRole('dialog')
    await fireEvent.update(within(dialog).getByLabelText('用户名或邮箱'), 'zh@example.com')
    await waitFor(() => expect(lookupUser).toHaveBeenCalledWith('zh@example.com'))
    expect(await within(dialog).findByText('张衡')).toBeTruthy()
    await fireEvent.click(within(dialog).getByRole('button', { name: '发送邀请' }))
    await waitFor(() => expect(inviteExternalMember).toHaveBeenCalledWith('p1', 'zhangheng'))
  })

  it('查无此人 → 说出来，并且按钮按不下去', async () => {
    lookupUser.mockRejectedValue(new ApiError(404, 'not found'))
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请外部成员'))
    const dialog = await screen.findByRole('dialog')
    await fireEvent.update(within(dialog).getByLabelText('用户名或邮箱'), 'nobody')
    expect(await within(dialog).findByText('没有找到这个用户名或邮箱')).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: '发送邀请' }).hasAttribute('disabled')).toBe(true)
  })

  it('已经在项目里的人不让再邀一次', async () => {
    lookupUser.mockResolvedValue({ handle: 'ligan', name: '李干', avatar_id: null })
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请外部成员'))
    const dialog = await screen.findByRole('dialog')
    await fireEvent.update(within(dialog).getByLabelText('用户名或邮箱'), 'ligan')
    expect(await within(dialog).findByText('已经在项目里')).toBeTruthy()
    expect(within(dialog).getByRole('button', { name: '发送邀请' }).hasAttribute('disabled')).toBe(true)
  })

  it('后端拒绝（比如对方已在团队里）时把理由写在输入框下面', async () => {
    lookupUser.mockResolvedValue({ handle: 'zhangheng', name: '张衡', avatar_id: null })
    inviteExternalMember.mockRejectedValue(new Error('这个人已经在团队里'))
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请外部成员'))
    const dialog = await screen.findByRole('dialog')
    await fireEvent.update(within(dialog).getByLabelText('用户名或邮箱'), 'zhangheng')
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
    await findByText('@zhangheng')
    expect(section(container, 'pending')?.textContent).toContain('外部')
    await fireEvent.click(await findByText('撤回'))
    await waitFor(() => expect(revokeInvitation).toHaveBeenCalledWith('inv1'))
  })
})
