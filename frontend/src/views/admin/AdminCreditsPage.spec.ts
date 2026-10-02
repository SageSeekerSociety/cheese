import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listPlans = vi.fn()
const listCreditTeams = vi.fn()
const getCreditTeam = vi.fn()
const getCreditTeamHistory = vi.fn()
const setCreditTeamPlan = vi.fn()
const grantTeamCredits = vi.fn()
const createPlan = vi.fn()
const updatePlan = vi.fn()

vi.mock('@/api/adminCredits', () => ({
  listPlans: (...a: unknown[]) => listPlans(...a),
  listCreditTeams: (...a: unknown[]) => listCreditTeams(...a),
  getCreditTeam: (...a: unknown[]) => getCreditTeam(...a),
  getCreditTeamHistory: (...a: unknown[]) => getCreditTeamHistory(...a),
  setCreditTeamPlan: (...a: unknown[]) => setCreditTeamPlan(...a),
  grantTeamCredits: (...a: unknown[]) => grantTeamCredits(...a),
  createPlan: (...a: unknown[]) => createPlan(...a),
  updatePlan: (...a: unknown[]) => updatePlan(...a),
}))

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return { ...actual, getGatewayModels: () => Promise.reject(new Error('offline')) }
})

// 键名透传：这一组问的是「调了什么、屏幕上剩下什么」，不是哪一句中文。
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key, locale: { value: 'zh-CN' } }) }
})

import AdminCreditsPage from './AdminCreditsPage.vue'

import { endOfDayIso } from '@/lib/adminCredits'

const FREE = {
  key: 'free',
  name: 'Free',
  audience: 'both',
  credits_per_period: 125,
  period: 'month',
  windows: [],
  model_tiers: ['included'],
  unlimited: false,
  admin_only: false,
  team_count: 3,
  is_default: true,
}
const RESERVE = {
  key: 'reserve',
  name: 'Reserve',
  audience: 'team',
  credits_per_period: null,
  period: 'month',
  windows: [],
  model_tiers: null,
  unlimited: true,
  admin_only: true,
  team_count: 0,
  is_default: false,
}

const PERIOD_PACK = {
  id: 'p1',
  source: 'plan_period',
  project_id: null,
  task_id: null,
  credits_total: 125,
  credits_used: 46,
  period_start: '2026-10-01T00:00:00+00:00',
  expires_at: null,
  reason: null,
  created_at: '2026-10-01T00:00:00+00:00',
}

const ROW = {
  id: 7,
  name: '个人',
  handle: 'linzy-personal',
  personal_owner: 'linzy',
  personal_owner_nickname: null,
  member_count: null,
  plan_key: 'free',
  period: { start: '2026-10-01T00:00:00+00:00', credits_total: 125, credits_used: 46 },
  packs: [PERIOD_PACK],
}

function detail(packs: Record<string, unknown>[] = [PERIOD_PACK]) {
  return { ...ROW, plan: FREE, packs }
}

function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const Wrapper = { components: { AdminCreditsPage }, template: '<v-app><AdminCreditsPage /></v-app>' }
  return render(Wrapper as unknown as Component, { global: { plugins: [vuetify] } })
}

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
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(() => {
  listPlans.mockReset().mockResolvedValue({ plans: [FREE, RESERVE] })
  listCreditTeams.mockReset().mockResolvedValue({ items: [ROW], total: 1, page: 1, page_size: 20 })
  getCreditTeam.mockReset().mockResolvedValue(detail())
  getCreditTeamHistory.mockReset().mockResolvedValue({ items: [] })
  setCreditTeamPlan.mockReset()
  grantTeamCredits.mockReset()
})
afterEach(cleanup)

async function openTeam(page: ReturnType<typeof mountPage>) {
  await fireEvent.click(await page.findByRole('button', { name: /@linzy/ }))
  await waitFor(() => expect(getCreditTeam).toHaveBeenCalledWith(7))
  await page.findByRole('button', { name: 'credits.panel.grant' })
}

describe('plans and credits', () => {
  it('issues credits with the entered amount, expiry and reason, and the panel shows the new pack', async () => {
    const granted = {
      ...PERIOD_PACK,
      id: 'g1',
      source: 'admin_grant',
      credits_total: 300,
      credits_used: 0,
      period_start: null,
      expires_at: endOfDayIso('2026-12-31'),
      reason: 'Midterm demo',
    }
    grantTeamCredits.mockResolvedValue(granted)
    const page = mountPage()
    await openTeam(page)
    getCreditTeam.mockResolvedValue(detail([PERIOD_PACK, granted]))

    await fireEvent.click(page.getByRole('button', { name: 'credits.panel.grant' }))
    const dialog = within(document.body)
    await fireEvent.update(await dialog.findByLabelText('credits.grantDialog.amount'), '300')
    // happy-dom 的 click 不派发 input，而单选框认的是 input：照浏览器的顺序补上。
    const onDate = dialog.getByRole('radio', { name: 'credits.grantDialog.onDate' }) as HTMLInputElement
    onDate.checked = true
    await fireEvent.input(onDate)
    await fireEvent.update(await dialog.findByLabelText('credits.grantDialog.date'), '2026-12-31')
    await fireEvent.update(dialog.getByLabelText('credits.grantDialog.reason'), 'Midterm demo')
    await fireEvent.click(dialog.getByRole('button', { name: 'credits.grantDialog.submit' }))

    await waitFor(() =>
      expect(grantTeamCredits).toHaveBeenCalledWith(7, {
        credits: 300,
        expires_at: endOfDayIso('2026-12-31'),
        reason: 'Midterm demo',
      })
    )
    expect(await dialog.findByText(/Midterm demo/)).toBeTruthy()
  })

  it('a grant without an expiry date is sent as never expiring', async () => {
    grantTeamCredits.mockResolvedValue({ ...PERIOD_PACK, id: 'g2', source: 'admin_grant' })
    const page = mountPage()
    await openTeam(page)

    await fireEvent.click(page.getByRole('button', { name: 'credits.panel.grant' }))
    const dialog = within(document.body)
    await fireEvent.update(await dialog.findByLabelText('credits.grantDialog.amount'), '50')
    await fireEvent.click(dialog.getByRole('button', { name: 'credits.grantDialog.submit' }))

    await waitFor(() =>
      expect(grantTeamCredits).toHaveBeenCalledWith(7, { credits: 50, expires_at: null, reason: null })
    )
  })

  it('a plan change the server rejects shows its reason and keeps the old plan', async () => {
    setCreditTeamPlan.mockRejectedValue(new Error('方案「Reserve」只给团队'))
    const page = mountPage()
    await openTeam(page)

    const body = within(document.body)
    const select = body.getAllByRole('combobox').find((el) => el.closest('.v-navigation-drawer'))
    await fireEvent.mouseDown(select as HTMLElement)
    const menu = await body.findByRole('listbox')
    await fireEvent.click(within(menu).getByText('Reserve'))

    await waitFor(() => expect(setCreditTeamPlan).toHaveBeenCalledWith(7, 'reserve'))
    expect(await body.findByText('方案「Reserve」只给团队')).toBeTruthy()
    const drawer = document.body.querySelector('.v-navigation-drawer') as HTMLElement
    expect(within(drawer).getByText('Free')).toBeTruthy()
    expect(within(drawer).queryByText('Reserve')).toBeNull()
  })

  it('searching asks the server for the first page of matching teams', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const page = mountPage()
      await page.findByRole('button', { name: /@linzy/ })
      await fireEvent.update(page.getByLabelText('credits.teams.search'), 'lin')
      await vi.advanceTimersByTimeAsync(400)
      await waitFor(() => expect(listCreditTeams).toHaveBeenLastCalledWith({ q: 'lin', page: 1, pageSize: 20 }))
    } finally {
      vi.useRealTimers()
    }
  })

  it('filtering by plan asks the server for the first page of teams on that plan', async () => {
    const page = mountPage()
    await page.findByRole('button', { name: /@linzy/ })

    const body = within(document.body)
    const [planFilter] = body.getAllByRole('combobox')
    await fireEvent.mouseDown(planFilter)
    const menu = await body.findByRole('listbox')
    await fireEvent.click(within(menu).getByText('Reserve'))

    await waitFor(() =>
      expect(listCreditTeams).toHaveBeenLastCalledWith(expect.objectContaining({ plan: 'reserve', page: 1 }))
    )
  })

  it("a team whose month's plan credits are not issued yet can spend the plan's monthly amount", async () => {
    listCreditTeams.mockResolvedValue({
      items: [{ ...ROW, period: { ...ROW.period, credits_total: null, credits_used: 0 }, packs: [] }],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const page = mountPage()
    await page.findByRole('button', { name: /@linzy/ })

    const teams = page.getByRole('table', { name: 'credits.teams.label' })
    const row = within(teams)
      .getByRole('button', { name: /@linzy/ })
      .closest('tr') as HTMLElement
    expect(within(row).getByText('125')).toBeTruthy()
  })
})
