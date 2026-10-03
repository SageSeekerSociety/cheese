import type { Component } from 'vue'
import type { Team } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const replace = vi.fn()
const route = { params: { handle: 'crew' }, query: {} as Record<string, string> }
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRoute: () => route,
  useRouter: () => ({ replace }),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMembers: vi.fn(async () => ({ data: { members: [] } })),
    listTeamJoinRequests: vi.fn(async () => ({ data: { applications: [] } })),
    listTeamInvitations: vi.fn(async () => ({ data: { invitations: [] } })),
    approveJoinRequest: vi.fn(),
  },
}))

import Members from './Members.vue'

import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { TeamsApi } from '@/network/api/teams'

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  route.query = {}
})

function mount(overrides: Partial<Team>) {
  setLocale('zh-CN')
  const team = ref({
    id: 7,
    name: 'Cheese 核心组',
    intro: '',
    avatarId: 1,
    owner: { id: 1 },
    // 第 4 个管理员不在 examples 里：只剩 role 能说明他是管理员。
    admins: { total: 4, examples: [] },
    members: { total: 0, examples: [] },
    visibility: 'public',
    ...overrides,
  } as unknown as Team)
  return render(Members as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: team },
      stubs: { TeamJoinLinkCard: { template: '<section>小队链接管理</section>' } },
    },
  })
}

describe('who manages the team link', () => {
  it('an admin who is not among the listed examples', async () => {
    mount({ role: 'ADMIN' })
    await screen.findByText('小队链接管理')
  })

  it('not an ordinary member', async () => {
    mount({ role: 'MEMBER' })
    await waitFor(() => screen.getByText('成员列表'))
    expect(screen.queryByText('小队链接管理')).toBeNull()
  })
})

describe('bringing people in', () => {
  it('the owner of a shared team can invite and review requests', async () => {
    mount({ role: 'OWNER' })
    await screen.findByRole('button', { name: /邀请成员/ })
    expect(screen.getByText('加入申请')).toBeTruthy()
    expect(screen.getByText('已发送邀请')).toBeTruthy()
  })
})

describe('under your own name', () => {
  it('there is no one to list or bring in: the page sends you to the projects', async () => {
    replace.mockClear()
    mount({ role: 'OWNER', personal: true, handle: 'linxia' })
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'TeamsDetailDefault', params: { handle: 'linxia' } })
    )
    expect(screen.queryByRole('button', { name: /邀请成员/ })).toBeNull()
  })
})

describe('answering a join request', () => {
  it('an approval the server answers with no body counts, and a second click sends nothing', async () => {
    const request = {
      id: 31,
      status: 'PENDING',
      createdAt: Date.now(),
      user: { id: 9, username: 'qinmo', nickname: 'qinmo' },
    }
    let approved = false
    vi.mocked(TeamsApi.listTeamJoinRequests).mockImplementation(
      async () =>
        ({
          data: { applications: [{ ...request, status: approved ? 'APPROVED' : 'PENDING' }] },
        }) as never
    )
    let answer!: () => void
    vi.mocked(TeamsApi.approveJoinRequest).mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = () => {
            approved = true
            // 204：响应体是空串
            resolve('' as never)
          }
        })
    )
    route.query = { tab: 'requests' }
    mount({ role: 'OWNER' })

    const approve = await screen.findByRole('button', { name: /批准/ })
    await fireEvent.click(approve)
    await fireEvent.click(approve)
    answer()

    await waitFor(() => expect(screen.queryByRole('button', { name: /批准/ })).toBeNull())
    expect(TeamsApi.approveJoinRequest).toHaveBeenCalledTimes(1)
    expect(TeamsApi.approveJoinRequest).toHaveBeenCalledWith(7, 31)
  })
})
