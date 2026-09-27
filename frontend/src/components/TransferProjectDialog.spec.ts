/** TransferProjectDialog：选一个接手的人、调接口、刷新，被拒时把理由留在弹窗里。
 *
 * 接得住项目的只有这个项目所属**团队**的成员（名册上 `source === 'team'`）——后端
 * `PUT /projects/{id}/owner` 就是这么要求的。所有者那一行、外部成员、AI 队友都不列，
 * 自己也不列。
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
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    setProjectOwner: (...a: unknown[]) => setProjectOwner(...a),
    listProjectMembers: (...a: unknown[]) => listProjectMembers(...a),
  }
})

const refreshMembers = vi.fn()
const refreshProjects = vi.fn()
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ refreshMembers, refreshProjects }),
}))

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

import TransferProjectDialog from './TransferProjectDialog.vue'

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
  setProjectOwner.mockReset().mockResolvedValue({})
  refreshMembers.mockReset().mockResolvedValue(undefined)
  refreshProjects.mockReset().mockResolvedValue(undefined)
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
  const utils = render(Host as unknown as Component, { global: { plugins: [vuetify] } })
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
})
