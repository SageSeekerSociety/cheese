import type { Component } from 'vue'
import type { Team } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const update = vi.fn()
// 这两个 mock 都为了同一件事：别让真实的网络客户端（它一转手就把 src/router 拉进来）
// 被加载 —— 那里 `createRouter` 在 vue-router 已被整包替掉之后会炸。
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { handle: 'cheese-core' }, name: 'TeamsDetailDefault', query: {} }),
  useRouter: () => ({ currentRoute: { value: { fullPath: '/teams/cheese-core' } }, replace: vi.fn() }),
}))
vi.mock('@/network/api/teams', () => ({ TeamsApi: { update: (...a: unknown[]) => update(...a) } }))
vi.mock('@/network/api/avatars', () => ({ AvatarsApi: { createAvatar: vi.fn() } }))

import DetailSidebar from './DetailSidebar.vue'

import { setLocale } from '@/i18n'

function team(overrides: Partial<Team> = {}): Team {
  return {
    id: 7,
    handle: 'cheese-core',
    name: 'Cheese 核心组',
    intro: '一起把芝士做完',
    avatarId: 3,
    owner: { id: 1, nickname: '队长' } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 1, examples: [] },
    role: 'OWNER',
    visibility: 'public',
    ...overrides,
  }
}

function mount(teamData: Team = team()) {
  return render(DetailSidebar as unknown as Component, {
    props: { teamData, teamMembersCount: 3 },
    global: {
      plugins: [createVuetify({ components, directives })],
      // 抽屉的开关和断点不是这里要验的东西；把槽放平，测试只谈侧栏头部。
      stubs: { SecondaryNavigation: { template: '<nav><slot /></nav>' } },
    },
  })
}

beforeAll(() => {
  // 同上：弹窗是 VOverlay，少了这两个全局对象它在 happy-dom 里根本不渲染。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})

beforeEach(() => {
  setLocale('zh-CN')
  update.mockReset().mockResolvedValue({ data: { team: team({ name: '芝士社' }) } })
})
afterEach(cleanup)

describe('the way in to editing a team', () => {
  it('gives an owner a pencil and opens the profile dialog from it', async () => {
    mount()
    await fireEvent.click(await screen.findByLabelText('编辑团队资料'))
    await screen.findByText('编辑团队资料')
    await waitFor(() => expect((screen.getByLabelText('团队名称') as HTMLInputElement).value).toBe('Cheese 核心组'))
  })

  it('gives an admin the same way in', async () => {
    mount(team({ role: 'ADMIN' }))
    await screen.findByLabelText('编辑团队资料')
  })

  it('leaves a plain member without one', async () => {
    mount(team({ role: 'MEMBER' }))
    await screen.findByText('Cheese 核心组')
    expect(screen.queryByLabelText('编辑团队资料')).toBeNull()
  })

  it('reports a saved team up to the page', async () => {
    const view = mount()
    await fireEvent.click(await screen.findByLabelText('编辑团队资料'))
    await fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    await waitFor(() => expect(view.emitted('updated')).toEqual([[team({ name: '芝士社' })]]))
  })
})

describe('what the sidebar says under the name', () => {
  it('shows a team its intro', async () => {
    mount()
    await screen.findByText('一起把芝士做完')
  })

  it('shows a personal team its own intro instead of the fixed line', async () => {
    mount(team({ personal: true, intro: '这是个只属于我的地方' }))
    await screen.findByText('这是个只属于我的地方')
    expect(screen.queryByText('你的个人团队')).toBeNull()
  })

  it('falls back to the fixed line for a personal team that has no intro', async () => {
    mount(team({ personal: true, intro: '' }))
    await screen.findByText('你的个人团队')
  })
})
