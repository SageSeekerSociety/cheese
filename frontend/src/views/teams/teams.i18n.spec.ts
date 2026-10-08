import type { Component } from 'vue'
import type { Knowledge } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'

vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRoute: () => ({ params: { handle: 'crew' }, query: {} }),
}))
vi.mock('@/api', () => ({
  ApiError: class extends Error {},
  authToken: () => '',
  BASE: '/api',
  listMyDevices: vi.fn(async () => ({ devices: [] })),
  listProjects: vi.fn(async () => ({ data: [{ id: 'p1', name: 'Alpha' }] })),
  listTeamDevices: vi.fn(async () => ({
    devices: [
      {
        device_id: 'laptop',
        name: 'laptop',
        online: true,
        project_ids: [],
        team_ids: [1],
        screens: [{ sid: 's1', agent_handle: 'helper' }],
        in_use: [],
      },
    ],
  })),
  listMyInvitations: vi.fn(async () => ({
    data: [{ id: 'inv1', project_name: '', inviter_handle: 'alice' }],
  })),
  registerDeviceForTeam: vi.fn(),
  respondToInvitation: vi.fn(),
  unregisterDeviceFromTeam: vi.fn(),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMembers: vi.fn(async () => ({
      data: {
        members: [
          { role: 'OWNER', user: { id: 1, nickname: 'Alice', avatarId: 1 } },
          { role: 'ADMIN', user: { id: 2, nickname: 'Bob', avatarId: 1 } },
        ],
      },
    })),
    listTeamJoinRequests: vi.fn(async () => ({ data: { applications: [] } })),
    listTeamInvitations: vi.fn(async () => ({ data: { invitations: [] } })),
    listMyJoinRequests: vi.fn(async () => ({
      data: {
        requests: [{ id: 1, status: 'PENDING', createdAt: 0, team: { name: 'Crew', avatarId: 1 } }],
      },
    })),
    listMyInvitations: vi.fn(async () => ({
      data: {
        invitations: [{ id: 2, status: 'PENDING', createdAt: 0, team: { name: 'Crew', avatarId: 1 } }],
      },
    })),
  },
}))

import Compute from './detail/Compute.vue'
import Members from './detail/Members.vue'
import Pending from './Pending.vue'

import KnowledgeEmpty from '@/components/teams/knowledge/KnowledgeEmpty.vue'
import KnowledgeTable from '@/components/teams/knowledge/KnowledgeTable.vue'
import KnowledgeToolbar from '@/components/teams/knowledge/KnowledgeToolbar.vue'
import i18n, { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const CJK = /[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]/
let previous: 'zh-CN' | 'en'

beforeAll(() => {
  previous = i18n.global.locale.value as 'zh-CN' | 'en'
  setLocale('en')
  vi.stubGlobal('devicePixelRatio', 1)
  // The compute page opens its team's live feed; nothing here answers it.
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 0
      send() {}
      close() {}
    }
  )
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
afterAll(() => setLocale(previous))

function mount(component: Component, props: Record<string, unknown> = {}) {
  return render(component, {
    props,
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: {
        [teamDataInjectionKey as symbol]: ref({
          id: 1,
          handle: 'crew',
          name: 'Crew',
          role: 'OWNER',
          owner: { id: 1 },
          admins: { total: 1, examples: [] },
          members: { total: 2, examples: [] },
        }),
      },
      stubs: {
        TeamJoinLinkCard: true,
        UserRef: { template: '<span>alice</span>' },
        UserRefLink: { template: '<span>alice</span>' },
      },
    },
  })
}

function expectNoChinese() {
  expect(document.body.textContent ?? '').not.toMatch(CJK)
}

it('renders the team compute page in English', async () => {
  mount(Compute)
  await screen.findByText('Running · @helper')
  expect(screen.getByText('Self-hosted devices')).toBeTruthy()
  expectNoChinese()
})

it('renders the team members page in English', async () => {
  mount(Members)
  await screen.findByText('Bob')
  expect(screen.getByText('Owner')).toBeTruthy()
  expect(screen.getByText('Join requests')).toBeTruthy()
  expectNoChinese()
})

it('renders the pending requests and invitations page in English', async () => {
  mount(Pending)
  await screen.findByText('A project')
  expect(screen.getByText('My requests')).toBeTruthy()
  expect(screen.getAllByText('Pending').length).toBe(2)
  expectNoChinese()
})

it('renders the knowledge toolbar, table and empty state in English', async () => {
  const resource = {
    id: 1,
    name: 'Notes',
    type: 'CODE',
    labels: ['guide'],
    creator: { id: 1, nickname: 'Alice', avatarId: 1 },
    createdAt: 0,
  } as unknown as Knowledge
  mount(KnowledgeToolbar, {
    searchQuery: null,
    typeFilter: null,
    tagFilter: null,
    viewMode: 'grid',
    resourceTypes: ['TEXT', 'MATERIAL', 'LINK', 'CODE'],
    availableTags: [],
  })
  mount(KnowledgeTable, { items: [resource], ownerId: 1 })
  mount(KnowledgeEmpty, { hasFilters: true })
  expect(await screen.findByText('Code snippet')).toBeTruthy()
  expect(screen.getByText('Upload resource')).toBeTruthy()
  expectNoChinese()
})
