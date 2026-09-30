// 首页那一格的目录：团队在原地展开成四样东西，正在看的那个团队一定是展开的；
// 团队操作只给这个团队的管理员。
import type { Component } from 'vue'
import type { Team } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getMyTeams = vi.fn()
vi.mock('@/network/api/teams', () => ({ TeamsApi: { getMyTeams: () => getMyTeams() } }))
vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { list: async () => ({ data: { spaces: [{ id: 3, name: '数据分析课' }] } }) },
}))

import HomeNav from './HomeNav.vue'

import { setLocale } from '@/i18n'

const blank = { template: '<div />' }

function team(handle: string, name: string, role: Team['role'] = 'MEMBER'): Team {
  return {
    id: handle.length,
    handle,
    name,
    intro: '',
    avatarId: 1,
    role,
    owner: { id: 1 } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 1, examples: [] },
  }
}

async function mount(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/inbox', name: 'inbox', component: blank },
      { path: '/spaces', name: 'HomeSpaces', component: blank },
      { path: '/spaces/:spaceId/board', name: 'SpaceBoardHome', component: blank },
      { path: '/teams/explore', name: 'HomeTeamsExplore', component: blank },
      {
        path: '/teams/:handle',
        component: { template: '<router-view />' },
        children: [
          { path: '', name: 'TeamsDetailDefault', component: blank },
          { path: 'members', name: 'TeamsDetailMembers', component: blank },
          { path: 'knowledge', name: 'TeamsDetailKnowledge', component: blank },
          { path: 'compute', name: 'TeamsDetailCompute', component: blank },
        ],
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  return render(HomeNav as unknown as Component, {
    props: { inbox: true },
    global: {
      plugins: [createVuetify({ components, directives }), router],
      stubs: { TeamProfileEditDialog: true, JoinSpaceDialog: true },
    },
  })
}

beforeAll(() => {
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
  localStorage.clear()
  getMyTeams
    .mockReset()
    .mockResolvedValue({ data: { teams: [team('crew', '知是开发组'), team('lab', '数据课第三组')] } })
})
afterEach(cleanup)

const hrefs = () => Array.from(document.querySelectorAll('a')).map((a) => a.getAttribute('href'))

describe('首页目录', () => {
  it('正在看的那个团队展开着，四样东西都点得到；别的团队收着', async () => {
    await mount('/teams/crew/members')
    await screen.findByText('知是开发组')
    await waitFor(() =>
      expect(hrefs()).toEqual(
        expect.arrayContaining(['/teams/crew', '/teams/crew/members', '/teams/crew/knowledge', '/teams/crew/compute'])
      )
    )
    expect(hrefs().some((href) => href?.startsWith('/teams/lab'))).toBe(false)
  })

  it('点一个团队在原地展开，再点收起', async () => {
    await mount('/inbox')
    await fireEvent.click(await screen.findByLabelText('展开 数据课第三组'))
    await waitFor(() => expect(hrefs()).toContain('/teams/lab/members'))
    await fireEvent.click(screen.getByLabelText('收起 数据课第三组'))
    await waitFor(() => expect(hrefs()).not.toContain('/teams/lab/members'))
  })

  it('团队操作只给这个团队的管理员', async () => {
    getMyTeams.mockResolvedValue({
      data: { teams: [team('crew', '知是开发组', 'ADMIN'), team('lab', '数据课第三组')] },
    })
    await mount('/inbox')
    await screen.findByText('数据课第三组')
    expect(screen.getAllByLabelText('团队操作')).toHaveLength(1)
  })

  it('空间点了就进那个空间', async () => {
    await mount('/inbox')
    await screen.findByText('数据分析课')
    expect(hrefs()).toContain('/spaces/3/board')
  })
})
