import type { Component } from 'vue'
import type { Team } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const replace = vi.fn()
vi.mock('vue-router', async () => ({
  ...(await vi.importActual<typeof import('vue-router')>('vue-router')),
  useRouter: () => ({ replace }),
}))
const removeMember = vi.fn()
const transferOwner = vi.fn()
const getMembers = vi.fn()
vi.mock('@/api/teams', () => ({
  TeamsApi: {
    removeMember: (...args: unknown[]) => removeMember(...args),
    transferOwner: (...args: unknown[]) => transferOwner(...args),
    getMembers: (...args: unknown[]) => getMembers(...args),
    del: vi.fn(),
  },
}))
vi.mock('@/services/account', () => ({ default: { user: { id: 1 } } }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ refreshProjects: vi.fn() }) }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const patchQuery = vi.fn(async () => {})
vi.mock('@/query/client', async () => ({
  ...(await vi.importActual<typeof import('@/query/client')>('@/query/client')),
  patchQuery: (...args: unknown[]) => patchQuery(...(args as [])),
}))

import Leave from './Leave.vue'

import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

function crew(role: Team['role']): Team {
  return {
    id: 7,
    handle: 'crew',
    name: '知是开发组',
    intro: '',
    avatarId: 0,
    owner: { id: 1 },
    admins: { total: 0, examples: [] },
    members: { total: 0, examples: [] },
    visibility: 'public',
    role,
  } as unknown as Team
}

function mount(team: Team) {
  setLocale('zh-CN')
  const injected = ref(team)
  render(Leave as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: injected },
    },
  })
  return injected
}

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', new EventTarget())
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
  vi.clearAllMocks()
})

describe('the last section of team settings', () => {
  it('the owner can transfer or disband, and cannot leave', () => {
    mount(crew('OWNER'))
    expect(screen.getByRole('heading', { level: 1, name: '转让与解散' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '转让' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '解散' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '退出' })).toBeNull()
  })

  it('a member who confirms leaving is taken off the team and sent to the inbox', async () => {
    removeMember.mockResolvedValue(undefined)
    mount(crew('MEMBER'))
    await fireEvent.click(screen.getByRole('button', { name: '退出' }))
    await fireEvent.click(await screen.findByRole('button', { name: '退出团队' }))

    await waitFor(() => expect(removeMember).toHaveBeenCalledWith(7, 1))
    await waitFor(() => expect(replace).toHaveBeenCalledWith({ name: 'inbox' }))
  })

  it('after a transfer the owner is an admin, and the section offers leaving instead', async () => {
    getMembers.mockResolvedValue({ members: [{ role: 'MEMBER', user: { id: 2, username: 'bob', nickname: 'Bob' } }] })
    transferOwner.mockResolvedValue({ team: crew('OWNER') })
    const team = mount(crew('OWNER'))
    await fireEvent.click(screen.getByRole('button', { name: '转让' }))
    await fireEvent.click(await screen.findByText('Bob'))
    await fireEvent.click(await screen.findByRole('button', { name: '转让团队' }))

    await waitFor(() => expect(transferOwner).toHaveBeenCalledWith(7, 2))
    await waitFor(() => expect(team.value.role).toBe('ADMIN'))
    await screen.findByRole('button', { name: '退出' })
  })
})
