/** 成员页：名册是「有谁」，右边那两个按钮是「找他」和「管他」。
 *
 * 这一份钉的是那些没有任何提示、错了也照样渲染的东西：管理动作对谁出现（后端
 * 只让 owner / lead 写，前端给不该有的人一个按钮 = 一个必定失败的按钮），所有者
 * 和自己那两行不能被降职或踢掉，以及私聊落在的地址和侧栏那条私聊行是同一个。
 */
import type { Component } from 'vue'
import type { ProjectMemberRow } from '@/cx_types'

import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'

const addProjectMember = vi.fn()
const updateProjectMemberRole = vi.fn()
const removeProjectMember = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    addProjectMember: (...a: unknown[]) => addProjectMember(...a),
    updateProjectMemberRole: (...a: unknown[]) => updateProjectMemberRole(...a),
    removeProjectMember: (...a: unknown[]) => removeProjectMember(...a),
  }
})

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push, replace: vi.fn() }), useRoute: () => ({ query: {} }) }))

let meHandle = 'alice'
vi.mock('@/me', () => ({ myHandle: () => meHandle }))

const refreshMembers = vi.fn()
let members: ProjectMemberRow[] = []
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    members,
    projects: [{ id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice' }],
    refreshMembers,
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
  addProjectMember.mockReset().mockResolvedValue({})
  updateProjectMemberRole.mockReset().mockResolvedValue({})
  removeProjectMember.mockReset().mockResolvedValue({ deleted: true })
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

function groupTitles(container: Element): string[] {
  return Array.from(container.querySelectorAll('.t-eyebrow')).map((n) => (n.textContent ?? '').trim())
}

describe('成员页', () => {
  it('人按角色分组，AI 队友单独一段——它没有可升降的角色', () => {
    const { container } = mount()
    const titles = groupTitles(container)
    expect(titles).toContain('组长 · 2')
    expect(titles).toContain('导师 · 1')
    expect(titles).toContain('成员 · 1')
    expect(titles).toContain('AI 队友 · 1')
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

  it('邀请带上选的角色，handle 前多打一个 @ 也认', async () => {
    const { getByText } = mount()
    await fireEvent.click(getByText('邀请成员'))
    await fireEvent.update(await screen.findByLabelText('handle'), '@zhangheng')
    await fireEvent.click(await screen.findByRole('button', { name: '邀请' }))
    await waitFor(() => expect(addProjectMember).toHaveBeenCalledWith('p1', 'zhangheng', 'member'))
  })
})
