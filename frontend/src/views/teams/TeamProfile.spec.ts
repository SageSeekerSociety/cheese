import type { Component } from 'vue'
import type { Team } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import TeamProfile from './TeamProfile.vue'

import { setLocale } from '@/i18n'

function team(overrides: Partial<Team> = {}): Team {
  return {
    id: 7,
    handle: 'zhishi',
    name: '知是',
    intro: '',
    avatarId: 1,
    owner: { id: 1, nickname: '芝士' } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 0, examples: [] },
    joinStatus: 'none',
    joinApproval: true,
    ...overrides,
  } as Team
}

async function mount(value: Team) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/teams/:handle', name: 'TeamsDetailDefault', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  return render(TeamProfile as unknown as Component, {
    props: { team: value, join: async () => {} },
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('a team profile', () => {
  it('says the handle the team goes by', async () => {
    await mount(team())
    screen.getByText('@zhishi')
  })

  it('takes a member to the team at its address', async () => {
    await mount(team({ joinStatus: 'member' }))
    const enter = await screen.findByRole('link', { name: '进入团队' })
    expect(enter.getAttribute('href')).toBe('/teams/zhishi')
  })
})
