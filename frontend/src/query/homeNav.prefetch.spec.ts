import type { Team } from '@/types'

import { defineComponent, effectScope, h, inject } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listAwaitingMe: vi.fn(),
  getUserProfile: vi.fn(),
  getUserTopics: vi.fn(),
  listTeamDevices: vi.fn(),
  getNotificationPreferences: vi.fn(),
  listNotifications: vi.fn(),
  getMyTeams: vi.fn(),
  detailByHandle: vi.fn(),
  listSpaces: vi.fn(),
  listApplications: vi.fn(),
}))

vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => '7' }))
vi.mock('@/composables/useChosenAvatar', () => ({
  ensureDefaultAvatarId: () => {},
  isChosenAvatar: () => false,
}))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listAwaitingMe: (...a: unknown[]) => api.listAwaitingMe(...a),
  getUserProfile: (...a: unknown[]) => api.getUserProfile(...a),
  getUserTopics: (...a: unknown[]) => api.getUserTopics(...a),
  listTeamDevices: (...a: unknown[]) => api.listTeamDevices(...a),
}))
vi.mock('@/api/notificationPreferences', () => ({
  getNotificationPreferences: (...a: unknown[]) => api.getNotificationPreferences(...a),
  saveNotificationPreferences: vi.fn(),
}))
vi.mock('@/api/notifications', () => ({
  NotificationsApi: {
    list: (...a: unknown[]) => api.listNotifications(...a),
  },
}))
vi.mock('@/api/teams', () => ({
  TeamsApi: {
    getMyTeams: (...a: unknown[]) => api.getMyTeams(...a),
    detailByHandle: (...a: unknown[]) => api.detailByHandle(...a),
  },
}))
vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    list: (...a: unknown[]) => api.listSpaces(...a),
    applications: (...a: unknown[]) => api.listApplications(...a),
  },
}))

import { useWaitingList } from '@/composables/useWaitingList'

import i18n from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import UserSettingsLayout from '@/layouts/user/Settings.vue'
import { queryClient } from '@/query/client'
import * as homeNav from '@/query/homeNav'
import { mySpacesQuery, myTeamsQuery } from '@/query/homeNav'
import { keys } from '@/query/keys'
import { forgetRoomSnapshots, readEarly } from '@/query/snapshot'

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
})

describe('personal and home route prefetching', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    queryClient.clear()
    forgetRoomSnapshots()
    for (const fn of Object.values(api)) fn.mockReset()
    api.listAwaitingMe.mockResolvedValue({ data: [], total: 0 })
    api.listNotifications.mockResolvedValue({
      notifications: [],
      page: { hasMore: false, nextStart: undefined },
    })
    api.getMyTeams.mockResolvedValue({
      teams: [{ id: 11, handle: 'alice', name: 'Alice', personal: true } as Team],
    })
    api.detailByHandle.mockResolvedValue({
      team: { id: 11, handle: 'alice', name: 'Alice', personal: true } as Team,
    })
    api.listTeamDevices.mockResolvedValue({ devices: [], can_manage: true })
    api.listSpaces.mockResolvedValue({ spaces: [] })
    api.listApplications.mockResolvedValue({ items: [] })
    api.getNotificationPreferences.mockResolvedValue({ events: {} })
  })

  afterEach(() => {
    cleanup()
    queryClient.clear()
    forgetRoomSnapshots()
  })

  it('prefetches inbox waiting list, teams and spaces once and reuses them on mount', async () => {
    if ('prefetchPersonalRoute' in homeNav) {
      homeNav.prefetchPersonalRoute('inbox', {})
    } else {
      readEarly(['inbox', 'awaiting-me'], () => api.listAwaitingMe())
      readEarly(keys.myTeams(), async () => (await api.getMyTeams()).teams)
      readEarly(keys.mySpaces(), async () => {
        await api.listSpaces({ pageSize: 50, sort_by: 'created_at', sort_order: 'desc' })
        return []
      })
    }
    expect(api.listAwaitingMe).toHaveBeenCalledTimes(1)
    expect(api.getMyTeams).toHaveBeenCalledTimes(1)
    expect(api.listSpaces).toHaveBeenCalledTimes(1)

    const scope = effectScope()
    const waiting = scope.run(() => useWaitingList())!
    await waiting.load()
    await queryClient.fetchQuery(myTeamsQuery())
    await queryClient.fetchQuery(mySpacesQuery())

    expect(api.listAwaitingMe).toHaveBeenCalledTimes(1)
    expect(api.getMyTeams).toHaveBeenCalledTimes(1)
    expect(api.listSpaces).toHaveBeenCalledTimes(1)
    scope.stop()
  })

  it('mounts UserSettingsCompute immediately from cached myTeams while detailByHandle is still in flight', async () => {
    queryClient.setQueryData<Team[]>(keys.myTeams(), [
      { id: 42, handle: 'alice', name: 'Alice', personal: true } as Team,
    ])
    // detailByHandle never resolves during this check: cached myTeams must unblock child mount.
    api.detailByHandle.mockReturnValue(new Promise(() => {}))

    const ComputeStub = defineComponent({
      setup() {
        const team = inject(teamDataInjectionKey)
        return () => h('div', { 'data-testid': 'compute-mounted' }, `team:${team?.value?.id ?? 'none'}`)
      },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/users/settings',
          name: 'UserSettings',
          component: UserSettingsLayout,
          children: [
            {
              path: 'compute',
              name: 'UserSettingsCompute',
              component: ComputeStub,
            },
          ],
        },
      ],
    })
    await router.push('/users/settings/compute')
    await router.isReady()

    const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
    render(Host, { global: { plugins: [vuetify, i18n, router, createPinia()] } })

    const mounted = await screen.findByTestId('compute-mounted')
    expect(mounted.textContent).toBe('team:42')
  })
})
