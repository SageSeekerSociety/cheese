/** TransferProjectDialog：选一个接手的人、调接口、刷新，被拒时把理由留在弹窗里。
 *
 * 名册上 `source === 'team'` 的别人列进候选人（所有者那一行、外部成员、AI 队友都不
 * 列，自己也不列）；名册上没人可交时，还能按完整的用户名或邮箱精确找到一个人。找到
 * 的人**不在这个项目的团队里**时，转让不是换个名字而是把项目整个搬走、转让人从此不
 * 在项目里 —— 所以那条路要再确认一次才真的发请求。
 *
 * mock 的行必须和 `GET /projects/{id}/members` 真实返回的形状一样（每一行都带
 * `source`）：以前这里的假数据是没有 `source` 的旧形状，于是「候选人」怎么筛都轮不到
 * 真实数据，测试一直绿而界面一直是空的。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const setProjectOwner = vi.fn()
const listProjectMembers = vi.fn()
const lookupUser = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    setProjectOwner: (...a: unknown[]) => setProjectOwner(...a),
    listProjectMembers: (...a: unknown[]) => listProjectMembers(...a),
    lookupUser: (...a: unknown[]) => lookupUser(...a),
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
  setProjectOwner.mockReset().mockResolvedValue({})
  refreshMembers.mockReset().mockResolvedValue(undefined)
  refreshProjects.mockReset().mockResolvedValue(undefined)
  // 默认「查无此人」：没写 mock 的用例里，查找也得有个确定的落点。
  lookupUser.mockReset().mockRejectedValue(new Error('没有这个账号'))
  listProjectMembers.mockReset().mockResolvedValue({
    data: [
      { user_handle: 'ligan', name: '李干', source: 'team', team_handle: 'zhishi', agent: false },
      { user_handle: 'alice', name: '爱丽丝', source: 'team', team_handle: 'zhishi', agent: false },
      { user_handle: 'owner-row', name: '补出来的', source: 'owner', agent: false },
      { user_handle: 'outsider', name: '外部的人', source: 'external', agent: false },
      { user_handle: 'cheese-x', name: '芝士', source: 'agent', agent: true },
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
  it('只列团队里的别人，不列自己、所有者、外部成员和 AI 队友', async () => {
    await mount()
    expect(await screen.findByText('李干')).toBeTruthy()
    expect(screen.queryByText('爱丽丝')).toBeNull()
    expect(screen.queryByText('补出来的')).toBeNull()
    expect(screen.queryByText('外部的人')).toBeNull()
    expect(screen.queryByText('芝士')).toBeNull()
  })

  it('团队里没有别人时说清楚没人接得住，而不是列一张空表', async () => {
    listProjectMembers.mockResolvedValue({
      data: [
        { user_handle: 'alice', name: '爱丽丝', source: 'team', team_handle: 'zhishi', agent: false },
        { user_handle: 'owner-row', name: '补出来的', source: 'owner', agent: false },
      ],
      total: 2,
    })
    await mount()
    expect(await screen.findByText('暂无可以接手的成员')).toBeTruthy()
  })

  // 自己名下的项目没有团队名册可挑：不说「团队里没人」，直接让人输入接手人。
  it('自己名下的项目直接找接手人，不提团队', async () => {
    projects.splice(0, projects.length, { id: 'p1', team_handle: 'alice', owner_handle: 'alice' })
    listProjectMembers.mockResolvedValue({
      data: [{ user_handle: 'alice', name: '爱丽丝', source: 'owner', agent: false }],
      total: 1,
    })
    await mount()
    expect(await screen.findByText('输入接手人的用户名或邮箱：')).toBeTruthy()
    expect(screen.queryByText(/团队/)).toBeNull()
    expect(screen.queryByText('暂无可以接手的成员')).toBeNull()
  })

  it('选好人确认之后交给他，并刷新项目行和名册', async () => {
    await mount()
    await fireEvent.click(await screen.findByText('李干'))
    await fireEvent.click(await screen.findByRole('button', { name: '转让' }))
    await waitFor(() => expect(setProjectOwner).toHaveBeenCalledWith('p1', 'ligan'))
    await waitFor(() => expect(refreshProjects).toHaveBeenCalled())
    expect(refreshMembers).toHaveBeenCalled()
  })

  it('被拒时弹窗不关，理由说在弹窗里', async () => {
    setProjectOwner.mockRejectedValue(new Error('他不在名册上'))
    await mount()
    await fireEvent.click(await screen.findByText('李干'))
    await fireEvent.click(await screen.findByRole('button', { name: '转让' }))
    expect(await screen.findByText('他不在名册上')).toBeTruthy()
    expect(screen.getByRole('button', { name: '转让' })).toBeTruthy()
    expect(refreshProjects).not.toHaveBeenCalled()
  })

  /** 按完整的用户名/邮箱找一个人；防抖过去、结果回来才算数。 */
  async function lookUp(query: string) {
    await fireEvent.update(screen.getByLabelText('用户名或邮箱（要写完整）'), query)
  }

  it('团队里没人可交时可以直接找一个人，找到的人能选中', async () => {
    lookupUser.mockResolvedValue({ handle: 'carol', name: '卡罗', avatar_id: null })
    await mount()

    await lookUp('carol')

    expect(await screen.findByText('卡罗')).toBeTruthy()
    expect(lookupUser).toHaveBeenCalledWith('carol')
  })

  it('找的是团队外的人：说清项目会跟着 TA 走，而且要再确认一次才发请求', async () => {
    lookupUser.mockResolvedValue({ handle: 'carol', name: '卡罗', avatar_id: null })
    await mount()
    await lookUp('carol')

    await fireEvent.click(await screen.findByText('卡罗'))
    // 团队外的人选中之后，弹窗里多出一条说明 —— 不是安静地换个名字。说明里的人按显示名写。
    const alert = screen.getAllByRole('alert').find((a) => a.textContent?.includes('转让会把项目整个搬到'))!
    expect(alert.textContent).toContain('搬到 @卡罗 名下')
    expect(alert.textContent).not.toContain('@carol')

    await fireEvent.click(screen.getByRole('button', { name: '转让' }))
    // 第一次点「转让」只提问，还没有真的转。
    expect(setProjectOwner).not.toHaveBeenCalled()

    await fireEvent.click(await screen.findByRole('button', { name: '确认转让' }))
    await waitFor(() => expect(setProjectOwner).toHaveBeenCalledWith('p1', 'carol'))
    await waitFor(() => expect(refreshProjects).toHaveBeenCalled())
  })

  it('确认那一步按「返回」退回选人：弹窗还开着，也没有转', async () => {
    lookupUser.mockResolvedValue({ handle: 'carol', name: '卡罗', avatar_id: null })
    await mount()
    await lookUp('carol')
    await fireEvent.click(await screen.findByText('卡罗'))
    await fireEvent.click(screen.getByRole('button', { name: '转让' }))

    await fireEvent.click(await screen.findByRole('button', { name: '返回' }))

    expect(await screen.findByRole('button', { name: '转让' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '确认转让' })).toBeNull()
    expect(setProjectOwner).not.toHaveBeenCalled()
  })

  it('团队里的成员接手是直接换人：没有第二步', async () => {
    await mount()
    await fireEvent.click(await screen.findByText('李干'))

    await fireEvent.click(screen.getByRole('button', { name: '转让' }))

    await waitFor(() => expect(setProjectOwner).toHaveBeenCalledWith('p1', 'ligan'))
    expect(screen.queryByRole('button', { name: '确认转让' })).toBeNull()
  })

  it('找到的人是自己时选不了：转让是把手交出去，不是左手倒右手', async () => {
    lookupUser.mockResolvedValue({ handle: 'alice', name: '爱丽丝', avatar_id: null })
    await mount()
    await lookUp('alice')

    await fireEvent.click(await screen.findByText('爱丽丝'))

    const submit = screen.getByRole('button', { name: '转让' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
  })

  it('找不到这个账号时说清楚，不列任何人', async () => {
    lookupUser.mockRejectedValue(new Error('没有这个账号'))
    await mount()

    await lookUp('nobody-at-all')

    expect(await screen.findByText('没有这个账号')).toBeTruthy()
  })
})
