import { nextTick, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  usage: vi.fn(),
  beta: vi.fn(),
  rewards: vi.fn(),
  claim: vi.fn(),
  requests: vi.fn(),
  request: vi.fn(),
}))
vi.mock('@/api/creditUsage', () => ({ getTeamCreditUsage: api.usage }))
vi.mock('@/api/creditOffers', () => ({
  getBetaCredits: api.beta,
  getMyCreditRewards: api.rewards,
  claimBetaCredits: api.claim,
  getCreditRequests: api.requests,
  createCreditRequest: api.request,
}))

import Credits from './Credits.vue'

import i18n, { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const usage = {
  plan: { key: 'free', name: 'Free', unlimited: false, credits_per_period: 500, windows: [], models: [] },
  period: { unlimited: false, credits_total: 500, credits_used: 0, used_ratio: 0, remaining_ratio: 1, resets_at: null },
  packs: [],
  windows: [],
  days: [],
  projects: [],
  lines: { collab: 0, ask: 0, write: 0, compute: 0 },
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
  vi.stubGlobal('visualViewport', { width: 1280, height: 800, addEventListener() {}, removeEventListener() {} })
})
beforeEach(() => {
  setLocale('zh-CN')
  vi.resetAllMocks()
  api.usage.mockResolvedValue(usage)
  api.beta.mockResolvedValue({
    credits: 500,
    claimed: false,
    feedback_credits: 100,
    plan_credits: 1000,
    invite_credits: 150,
    invitee_credits: 150,
  })
  api.rewards.mockResolvedValue({ feedback_count: 0, feedback_credits: 0, items: [] })
  api.requests.mockResolvedValue({ items: [] })
  api.claim.mockResolvedValue({ credits: 500, issued: true })
})
afterEach(cleanup)

async function show() {
  const team = ref({ id: 2, handle: 'alice', personal: true, role: 'OWNER' })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/plans', name: 'CreditPlans', component: { template: '<div />' } },
      { path: '/feedback', component: { template: '<div />' } },
    ],
  })
  const view = render(Credits, {
    global: {
      plugins: [createVuetify({ components, directives }), router, i18n],
      provide: { [teamDataInjectionKey as symbol]: team },
    },
  })
  await waitFor(() => expect(api.usage).toHaveBeenCalledWith(2))
  return { view, team }
}

it('claims the personal reward and refreshes the personal team balance', async () => {
  const { view } = await show()
  await fireEvent.click(await view.findByRole('button', { name: '领取 500 点' }))
  await waitFor(() => expect(api.claim).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(api.usage).toHaveBeenCalledTimes(2))
  expect(api.usage.mock.calls).toEqual([[2], [2]])
  expect(api.requests).toHaveBeenCalledWith(2)
  await fireEvent.click(view.getByRole('button', { name: '充值' }))
  await fireEvent.update(await view.findByRole('spinbutton', { name: /^充值点数/ }), '1100')
  await fireEvent.click(view.getByRole('button', { name: '提交充值申请' }))
  await waitFor(() => expect(api.request).toHaveBeenCalledExactlyOnceWith(2, 'top_up', 1100))
})

it('switches to shared team requests without exposing personal rewards', async () => {
  const { view, team } = await show()
  await view.findByRole('button', { name: '领取 500 点' })
  team.value = { id: 3, handle: 'lab', personal: false, role: 'OWNER' }
  await waitFor(() => expect(api.requests).toHaveBeenCalledWith(3))
  expect(view.queryByRole('button', { name: '领取 500 点' })).toBeNull()
  await fireEvent.click(view.getByRole('button', { name: '充值' }))
  await fireEvent.update(await view.findByRole('spinbutton', { name: /^充值点数/ }), '1200')
  await fireEvent.click(view.getByRole('button', { name: '提交充值申请' }))
  await waitFor(() => expect(api.request).toHaveBeenCalledExactlyOnceWith(3, 'top_up', 1200))
  expect(api.claim).not.toHaveBeenCalled()
})

it('keeps the selected team balance when a previous team response arrives late', async () => {
  let resolvePersonal!: (value: typeof usage) => void
  api.usage.mockImplementation((id: number) =>
    id === 2
      ? new Promise((resolve) => {
          resolvePersonal = resolve
        })
      : Promise.resolve({ ...usage, plan: { ...usage.plan, name: 'Shared plan' } })
  )
  const { view, team } = await show()
  team.value = { id: 3, handle: 'lab', personal: false, role: 'OWNER' }
  await view.findByRole('button', { name: /Shared plan/ })
  resolvePersonal(usage)
  await Promise.resolve()
  await nextTick()
  await waitFor(() => expect(view.queryByRole('button', { name: /^Free/ })).toBeNull())
  expect(view.getByRole('button', { name: /Shared plan/ })).toBeTruthy()
})

it('warns at the top when the month is 80% spent', async () => {
  api.usage.mockResolvedValue({
    ...usage,
    period: { ...usage.period, credits_used: 400, used_ratio: 0.8, remaining_ratio: 0.2 },
  })
  const { view } = await show()
  expect(await view.findByText('团队额度快用完了')).toBeTruthy()
  expect(view.getByText(/方案额度还剩 100 点/)).toBeTruthy()
})

it('names the window in the warning instead of 「这个窗口」', async () => {
  api.usage.mockResolvedValue({
    ...usage,
    period: {
      ...usage.period,
      unlimited: true,
      credits_total: null,
      credits_used: null,
      used_ratio: null,
      remaining_ratio: null,
    },
    windows: [
      {
        hours: null,
        calendar: 'week',
        credits_total: 1000,
        credits_used: 900,
        used_ratio: 0.9,
        resets_at: '2026-11-02T00:00:00+08:00',
      },
    ],
  })
  const { view } = await show()
  expect(await view.findByText('团队额度快用完了')).toBeTruthy()
  expect(view.getByText(/本周的方案额度还剩/)).toBeTruthy()
  expect(view.queryByText(/这个窗口/)).toBeNull()
})
