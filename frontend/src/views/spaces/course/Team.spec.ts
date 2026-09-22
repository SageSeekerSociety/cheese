// 「我的小组」（学生）那一屏。
//
// 三件事：我在这门课的组（服务端按我的项目取 teamId）、别人拉我 / 我在申请的
// 两件等待、以及没有组时的出路（自己建一个）。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getMyCourseGroup = vi.fn()
const getMyTeams = vi.fn()
const listMyInvitations = vi.fn()
const listMyJoinRequests = vi.fn()
const createTeam = vi.fn()
const acceptInvitation = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getMyCourseGroup: (...a: unknown[]) => getMyCourseGroup(...a),
    getCourseRoster: vi.fn(),
  },
}))

vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMyTeams: (...a: unknown[]) => getMyTeams(...a),
    listMyInvitations: (...a: unknown[]) => listMyInvitations(...a),
    listMyJoinRequests: (...a: unknown[]) => listMyJoinRequests(...a),
    create: (...a: unknown[]) => createTeam(...a),
    acceptInvitation: (...a: unknown[]) => acceptInvitation(...a),
    declineInvitation: vi.fn(),
    cancelMyJoinRequest: vi.fn(),
  },
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import Team from './Team.vue'

const ALICE = { id: 11, username: 'alice', nickname: 'Alice' }

function emptyTeams() {
  getMyTeams.mockResolvedValue({ data: { teams: [] } })
  listMyInvitations.mockResolvedValue({ data: { invitations: [] } })
  listMyJoinRequests.mockResolvedValue({ data: { requests: [] } })
}

async function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId', name: 'SpacesDetail', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/course/team', name: 'SpacesCourseTeam', component: { template: '<div />' } },
    ],
  })
  await router.push('/spaces/1/course/team')
  await router.isReady()
  return render(Team, { global: { plugins: [vuetify, router, createPinia()] } })
}

beforeAll(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  getMyCourseGroup.mockReset()
  getMyTeams.mockReset()
  listMyInvitations.mockReset()
  listMyJoinRequests.mockReset()
  createTeam.mockReset().mockResolvedValue({ data: { team: { id: 9 } } })
  acceptInvitation.mockReset().mockResolvedValue({})
})

afterEach(cleanup)

describe('my group in a course', () => {
  it('shows the group the course reports, with its members', async () => {
    getMyCourseGroup.mockResolvedValue({
      data: { projectId: 'p-1', team: { id: 5, name: '第一组', members: [ALICE] } },
    })
    emptyTeams()
    const page = await mountPage()
    await waitFor(() => expect(page.getByText('第一组')).toBeTruthy())
    expect(getMyCourseGroup).toHaveBeenCalledWith(1)
    expect(page.getByText('Alice')).toBeTruthy()
  })

  it('offers to create a group when the student is in none', async () => {
    getMyCourseGroup.mockResolvedValue({ data: { projectId: null, team: null } })
    emptyTeams()
    const page = await mountPage()
    await waitFor(() => expect(page.getByText('spaces.course.team.noGroupYet')).toBeTruthy())
    await fireEvent.click(page.getByRole('button', { name: 'spaces.course.team.create' }))
    const input = await waitFor(() => {
      const el = document.querySelector('input[type="text"]')
      expect(el).toBeTruthy()
      return el as HTMLInputElement
    })
    await fireEvent.update(input, '第二组')
    await fireEvent.click(page.getByRole('button', { name: 'spaces.course.team.confirmCreate' }))
    await waitFor(() =>
      expect(createTeam).toHaveBeenCalledWith({
        name: '第二组',
        intro: '',
        description: '',
        avatarId: 1,
      })
    )
  })

  it('accepts an invitation someone sent the student', async () => {
    getMyCourseGroup.mockResolvedValue({ data: { projectId: null, team: null } })
    getMyTeams.mockResolvedValue({ data: { teams: [] } })
    listMyInvitations.mockResolvedValue({
      data: {
        invitations: [
          {
            id: 31,
            team: { id: 5, name: '第一组' },
            initiator: { id: 12, username: 'bob', nickname: 'Bob' },
          },
        ],
      },
    })
    listMyJoinRequests.mockResolvedValue({ data: { requests: [] } })
    const page = await mountPage()
    await waitFor(() => expect(page.getByText('spaces.course.team.invitations')).toBeTruthy())
    await fireEvent.click(page.getByRole('button', { name: 'spaces.course.team.accept' }))
    await waitFor(() => expect(acceptInvitation).toHaveBeenCalledWith(31))
  })
})
