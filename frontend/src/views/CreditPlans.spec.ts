import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ request: vi.fn(), teams: vi.fn(), usage: vi.fn() }))
vi.mock('@/api/creditOffers', () => ({
  getPublishedCreditPlans: async () => ({ plans: [] }),
  getBetaCredits: async () => ({ credits: 500, feedback_credits: 100, claimed: false }),
  getCreditRequests: async () => ({ items: [] }),
  createCreditRequest: api.request,
}))
vi.mock('@/api/teams', () => ({ TeamsApi: { getMyTeams: api.teams } }))
vi.mock('@/api/creditUsage', () => ({ getTeamCreditUsage: api.usage }))

import CreditPlans from './CreditPlans.vue'

import CreditTopUp from '@/components/usage/CreditTopUp.vue'
import i18n, { setLocale } from '@/i18n'
import FeedbackCenterPageView from '@/views/feedback/FeedbackCenterPageView.vue'
import CreditsView from '@/views/teams/detail/CreditsView.vue'

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', { width: 1280, height: 800, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  setLocale('zh-CN')
  api.request.mockReset().mockResolvedValue({})
  api.teams.mockResolvedValue({
    teams: [
      { id: 2, handle: 'alice', name: 'Alice', role: 'OWNER', personal: true },
      { id: 3, handle: 'citylab', name: 'City Lab', role: 'OWNER', personal: false },
      { id: 4, handle: 'robots', name: 'Robotics', role: 'MEMBER', personal: false },
    ],
  })
  api.usage.mockReset().mockResolvedValue({ plan: { key: 'free', name: 'Free' } })
})
afterEach(cleanup)

async function plans(query = { team: 'citylab' }) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/plans', name: 'CreditPlans', component: CreditPlans },
      { path: '/teams/:handle/credits', name: 'TeamsDetailCredits', component: { template: '<div />' } },
      { path: '/', name: 'HomeHub', component: { template: '<div />' } },
    ],
  })
  await router.push({ name: 'CreditPlans', query })
  const view = render(
    { template: '<v-app><router-view /></v-app>' },
    {
      global: { plugins: [createVuetify({ components, directives }), router, i18n] },
    }
  )
  return { ...view, router }
}

describe('Plans and top-ups', () => {
  it('keeps personal plans and the return link scoped to the personal team', async () => {
    const view = await plans({ team: 'alice' })
    await waitFor(() => expect(api.usage).toHaveBeenCalledExactlyOnceWith(2))
    expect(view.getByRole('link', { name: '返回额度' }).getAttribute('href')).toBe('/teams/alice/credits')
    expect(view.queryByRole('button', { name: '申请 Team 方案' })).toBeNull()
    expect(view.getByLabelText('查看对象').textContent).toContain('Alice')
  })

  it('links from team credits to plans with the team preserved', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/plans', name: 'CreditPlans', component: { template: '<div />' } }],
    })
    const view = render(CreditsView, {
      props: { usage: null, loading: false, error: null, teamHandle: 'citylab' },
      global: { plugins: [createVuetify({ components, directives }), router, i18n] },
    })
    expect(view.getByRole('link', { name: '查看套餐' }).getAttribute('href')).toBe('/plans?team=citylab')
  })

  it('opens reward rules when arriving from another page and shows only awarded feedback', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/feedback', component: { template: '<div />' } },
        { path: '/feedback/new', name: 'FeedbackSubmit', component: { template: '<div />' } },
        { path: '/teams/:handle/credits', name: 'TeamsDetailCredits', component: { template: '<div />' } },
      ],
    })
    await router.push('/feedback#rewards')
    const view = render(FeedbackCenterPageView, {
      props: {
        isAdmin: false,
        personalTeamHandle: 'alice',
        tab: 'all',
        tabs: [],
        meta: null,
        query: '',
        filterKind: null,
        filterStatus: null,
        filterDays: null,
        filterAuthor: '',
        hasFilters: false,
        items: [],
        total: 0,
        loading: false,
        loadingMore: false,
        listHasMore: false,
        error: null,
        hotHalfLifeDays: 2,
        hotThreshold: 10,
        hotMinItems: 5,
        beta: { credits: 500, claimed: true, feedback_credits: 100 },
        rewards: { feedback_count: 2, feedback_credits: 200, items: [] },
      },
      global: { plugins: [createVuetify({ components, directives }), router, i18n] },
    })
    expect(view.getByRole('button', { name: '活动规则' }).getAttribute('aria-expanded')).toBe('true')
    expect(view.getByText('2 条')).toBeTruthy()
    expect(view.getByText('200 点')).toBeTruthy()
    expect(view.getByRole('link', { name: '前往个人额度领取' }).getAttribute('href')).toBe('/teams/alice/credits')
  })

  it('keeps the selected team when returning and sends a request only for that team', async () => {
    const view = await plans()
    const apply = await view.findByRole('button', { name: '申请 Team 方案' })
    await waitFor(() => expect(apply.hasAttribute('disabled')).toBe(false))
    expect(view.getByRole('link', { name: '返回额度' }).getAttribute('href')).toBe('/teams/citylab/credits')
    await fireEvent.click(apply)
    await waitFor(() => expect(api.request).toHaveBeenCalledExactlyOnceWith(3, 'team_plan'))
    await fireEvent.update(view.getByLabelText('查看对象'), 'robots')
    await waitFor(() => expect(view.router.currentRoute.value.query.team).toBe('robots'))
    expect(view.queryByRole('button', { name: '申请 Team 方案' })).toBeNull()
    expect(api.request).toHaveBeenCalledTimes(1)
  })

  it('prevents requests until the selected team has finished loading', async () => {
    api.usage.mockReturnValue(new Promise(() => {}))
    const view = await plans()
    const apply = await view.findByRole('button', { name: '申请 Team 方案' })
    expect(apply.hasAttribute('disabled')).toBe(true)
    await fireEvent.click(apply)
    expect(api.request).not.toHaveBeenCalled()
  })

  it('cancels a top-up without submitting and submits the entered amount once', async () => {
    const request = vi.fn()
    const view = render(
      {
        components: { CreditTopUp },
        template: '<v-app><CreditTopUp @request="request" /></v-app>',
        setup: () => ({ request }),
      },
      { global: { plugins: [createVuetify({ components, directives }), i18n] } }
    )
    await fireEvent.click(view.getByRole('button', { name: '充值' }))
    const field = await view.findByRole('spinbutton', { name: /^充值点数/ })
    await fireEvent.update(field, '1200')
    await fireEvent.click(view.getByRole('button', { name: '取消' }))
    expect(request).not.toHaveBeenCalled()
    await fireEvent.click(view.getByRole('button', { name: '充值' }))
    await fireEvent.click(await view.findByRole('button', { name: '提交充值申请' }))
    expect(request).toHaveBeenCalledExactlyOnceWith(1200)
  })
})
