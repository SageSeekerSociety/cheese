import type { Component } from 'vue'
import type { Team } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRoute: () => ({ params: { teamId: '7' }, query: {} }),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMembers: vi.fn(async () => ({ data: { members: [] } })),
    listTeamJoinRequests: vi.fn(async () => ({ data: { applications: [] } })),
    listTeamInvitations: vi.fn(async () => ({ data: { invitations: [] } })),
  },
}))

import Members from './Members.vue'

import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

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
afterEach(cleanup)

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

  it('nobody on a personal team', async () => {
    mount({ role: 'OWNER', personal: true })
    await waitFor(() => screen.getByText('成员列表'))
    expect(screen.queryByText('小队链接管理')).toBeNull()
  })
})
