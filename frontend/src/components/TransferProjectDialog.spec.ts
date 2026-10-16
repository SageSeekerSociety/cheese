/** TransferProjectDialog：选一个接手的人，发出转让，被拒时把理由留在弹窗里（#3830）。
 *
 * 只能交给项目里的管理员或成员：自己、所有者那一行、导师、AI 队友都不列。转让只是
 * 「发出」——对方接受之前什么都不变，所以不再有找人框和第二步确认。
 *
 * mock 的行必须和 `GET /projects/{id}/members` 真实返回的形状一样（每一行带 `role`）：
 * 假数据少了这个字段，候选人怎么筛都轮不到真实数据，测试一直绿而界面一直是空的。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const offerTransfer = vi.fn()
const listProjectMembers = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    offerTransfer: (...a: unknown[]) => offerTransfer(...a),
    listProjectMembers: (...a: unknown[]) => listProjectMembers(...a),
  }
})

const refreshMembers = vi.fn()
const refreshProjects = vi.fn()
// 项目行：p1 默认挂在共享团队 zhishi 下；自己名下的项目，团队地址就是所有者的用户名。
const projects: { id: string; team_handle: string; owner_handle: string }[] = []
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ refreshMembers, refreshProjects, projects }),
}))

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

import TransferProjectDialog from './TransferProjectDialog.vue'

import i18n, { setLocale } from '@/i18n'

const Dialog = TransferProjectDialog as unknown as Component

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
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  projects.splice(0, projects.length, { id: 'p1', team_handle: 'zhishi', owner_handle: 'alice' })
  offerTransfer
    .mockReset()
    .mockResolvedValue({ id: 't1', project_id: 'p1', from_handle: 'alice', to_handle: 'ligan', status: 'pending' })
  refreshMembers.mockReset().mockResolvedValue(undefined)
  refreshProjects.mockReset().mockResolvedValue(undefined)
  listProjectMembers.mockReset().mockResolvedValue({
    data: [
      { user_handle: 'alice', name: '爱丽丝', source: 'owner', role: 'owner', agent: false },
      { user_handle: 'ligan', name: '李干', source: 'team', team_handle: 'zhishi', role: 'member', agent: false },
      { user_handle: 'boss', name: '管事的', source: 'team', team_handle: 'zhishi', role: 'admin', agent: false },
      { user_handle: 'teacher', name: '老师', source: 'external', role: 'mentor', agent: false },
      { user_handle: 'cheese-x', name: '芝士', source: 'agent', role: 'member', agent: true },
    ],
    total: 5,
  })
})

/** 弹窗在打开的那一刻拉名册，所以先关着挂上，再打开。 */
async function mount() {
  const Host = {
    setup() {
      const open = ref(false)
      return { open }
    },
    components: { Dialog },
    template: `<div><button type="button" data-testid="open" @click="open = true">打开</button><Dialog v-model="open" project-id="p1" /></div>`,
  }
  const utils = render(Host as unknown as Component, { global: { plugins: [vuetify, i18n] } })
  await fireEvent.click(utils.getByTestId('open'))
  return utils
}

describe('TransferProjectDialog', () => {
  it('只列项目里的管理员和成员，不列自己、导师和 AI 队友', async () => {
    await mount()
    expect(await screen.findByText('李干')).toBeTruthy()
    expect(screen.getByText('管事的')).toBeTruthy()
    expect(screen.queryByText('爱丽丝')).toBeNull()
    expect(screen.queryByText('老师')).toBeNull()
    expect(screen.queryByText('芝士')).toBeNull()
  })

  it('没有人接得住时说清楚，而不是列一张空表', async () => {
    listProjectMembers.mockResolvedValue({
      data: [
        { user_handle: 'alice', name: '爱丽丝', source: 'owner', role: 'owner', agent: false },
        { user_handle: 'teacher', name: '老师', source: 'external', role: 'mentor', agent: false },
      ],
      total: 2,
    })
    await mount()
    expect(await screen.findByText('暂无可以接手的人')).toBeTruthy()
  })

  it('没选人之前发不出去', async () => {
    await mount()
    await screen.findByText('李干')
    expect((screen.getByRole('button', { name: '发出转让' }) as HTMLButtonElement).disabled).toBe(true)
    expect(offerTransfer).not.toHaveBeenCalled()
  })

  it('自己名下的项目说清楚转让后会移到对方名下', async () => {
    projects.splice(0, projects.length, { id: 'p1', team_handle: 'alice', owner_handle: 'alice' })
    await mount()
    expect(await screen.findByText('这是你个人名下的项目，转让后会移到对方名下')).toBeTruthy()
  })

  it('团队项目不提移到对方名下', async () => {
    await mount()
    await screen.findByText('李干')
    expect(screen.queryByText(/移到对方名下/)).toBeNull()
  })

  it('选好人一次就发出转让，弹窗收起', async () => {
    await mount()
    await fireEvent.click(await screen.findByText('李干'))
    await fireEvent.click(screen.getByRole('button', { name: '发出转让' }))
    await waitFor(() => expect(offerTransfer).toHaveBeenCalledWith('p1', 'ligan'))
    expect(offerTransfer).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('button', { name: '发出转让' })).toBeNull())
  })

  it('被拒时弹窗不关，理由说在弹窗里', async () => {
    offerTransfer.mockRejectedValue(new Error('他不在名册上'))
    await mount()
    await fireEvent.click(await screen.findByText('李干'))
    await fireEvent.click(screen.getByRole('button', { name: '发出转让' }))
    expect(await screen.findByText('他不在名册上')).toBeTruthy()
    expect(screen.getByRole('button', { name: '发出转让' })).toBeTruthy()
  })
})
