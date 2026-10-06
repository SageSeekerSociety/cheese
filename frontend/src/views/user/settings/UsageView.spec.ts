// 「芝士额度」: what a person sees of their month — how many points are used and
// left, when it is used up and when it resets, the credits besides the plan's
// only when there are some, what the plan includes, and a way to each of their
// teams' pages.
import type { Component } from 'vue'
import type { CreditUsage } from '@/lib/creditUsage'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import UsageView from './UsageView.vue'

import i18n, { setLocale } from '@/i18n'

function days(): CreditUsage['days'] {
  return Array.from({ length: 31 }, (_, i) => ({
    date: `2026-10-${String(i + 1).padStart(2, '0')}`,
    credits: i < 2 ? 145 : i < 20 ? 0 : null,
    lines: { collab: i < 2 ? 145 : 0, ask: 0, write: 0, compute: 0 },
  }))
}

const PERIOD = {
  unlimited: false,
  credits_total: 500,
  credits_used: 290,
  used_ratio: 0.58,
  remaining_ratio: 0.42,
  resets_at: '2026-10-31T16:00:00+00:00',
}

const PACK = {
  id: 'g1',
  source: 'admin_grant',
  project_id: null,
  task_id: null,
  project_name: null,
  task_name: null,
  credits_total: 300,
  credits_remaining: 210,
  remaining_ratio: 0.7,
  expires_at: null,
}

function usage(over: Partial<CreditUsage> = {}): CreditUsage {
  return {
    plan: {
      key: 'free',
      name: 'Free',
      unlimited: false,
      credits_per_period: 500,
      windows: [],
      models: ['DeepSeek Flash'],
    },
    period: PERIOD,
    windows: [],
    packs: [],
    days: days(),
    projects: [{ id: 'p1', name: '空气质量看板', credits: 290 }],
    lines: { collab: 134, ask: 87, write: 29, compute: 40 },
    teams: [
      {
        id: 3,
        name: '城市数据实验室',
        handle: 'citylab',
        plan: { key: 'free', name: 'Free' },
        unlimited: false,
        remaining_ratio: 0.08,
        credits_remaining: 40,
      },
    ],
    ...over,
  }
}

function show(data: CreditUsage) {
  const blank = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: blank },
      { path: '/teams/:handle/credits', name: 'TeamsDetailCredits', component: blank },
      { path: '/projects/:projectId', name: 'workspace-project', component: blank },
    ],
  })
  return render(UsageView as unknown as Component, {
    props: { usage: data, loading: false, error: null },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
}

beforeAll(() => {
  // The plan's details open in a menu, which measures the viewport.
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
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('芝士额度', () => {
  it('a used-up month says so and when it resets', async () => {
    const view = show(usage({ period: { ...PERIOD, credits_used: 500, used_ratio: 1, remaining_ratio: 0 } }))

    expect(await view.findAllByText('本月已用完')).not.toHaveLength(0)
    expect(view.getByText(/重置/)).toBeTruthy()
    expect(view.queryByText(/还剩/)).toBeNull()
  })

  it('a used-up month with other credits left says those are in use', async () => {
    const view = show(
      usage({ period: { ...PERIOD, credits_used: 500, used_ratio: 1, remaining_ratio: 0 }, packs: [PACK] })
    )

    const month = await view.findByRole('region', { name: '本月' })
    expect(within(month).queryByText('本月已用完')).toBeNull()
    expect(within(month).getAllByText(/正在使用其他额度/)).not.toHaveLength(0)
  })

  it('a plan limited by time windows is never shown as unlimited', async () => {
    const view = show(
      usage({
        period: null,
        windows: [
          { hours: 5, calendar: null, used_ratio: 0.4, resets_at: '2026-10-03T10:00:00+00:00' },
          { hours: null, calendar: 'month', used_ratio: 0.1, resets_at: '2026-10-31T16:00:00+00:00' },
        ],
      })
    )

    const plan = await view.findByRole('region', { name: '使用上限' })
    expect(within(plan).queryByText('不限')).toBeNull()
    expect(within(plan).getByText('5 小时内已用 40%')).toBeTruthy()
    expect(within(plan).getByText('本月已用 10%')).toBeTruthy()
    expect(within(plan).getAllByText(/重置/)).toHaveLength(2)
  })

  it("the month reads in points: used of the plan's, what is left and each line's", async () => {
    const view = show(usage())
    const month = await view.findByRole('region', { name: '本月' })
    expect(within(month).getByText(/290\D+500/)).toBeTruthy()
    expect(within(month).getByText(/210/)).toBeTruthy()
    for (const spent of ['134', '87', '29', '40'])
      expect(within(month).getByText(new RegExp(`^${spent}\\D`))).toBeTruthy()
  })

  it('cloud compute is a line of its own, next to the model lines', async () => {
    const view = show(usage())
    const month = await view.findByRole('region', { name: '本月' })
    const compute = within(month).getByText(/算力/)
    expect(compute.textContent).toMatch(/40/)
  })

  it("the plan's name opens what the plan includes", async () => {
    const view = show(usage())
    const month = await view.findByRole('region', { name: '本月' })
    await fireEvent.click(within(month).getByRole('button', { name: /Free/ }))

    const details = within(document.body)
    expect(await details.findByText(/DeepSeek Flash/)).toBeTruthy()
    expect(details.getByText(/每月 500/)).toBeTruthy()
  })

  it('shows the other credits only when there are some', async () => {
    const without = show(usage())
    await without.findByText('我的项目')
    expect(without.queryByText('其他额度')).toBeNull()
    cleanup()

    const withPack = show(usage({ packs: [PACK] }))
    expect(await withPack.findByText('其他额度')).toBeTruthy()
    expect(withPack.getByText('管理员发放')).toBeTruthy()
  })

  it("each of the person's teams leads to that team's credits page", async () => {
    const view = show(usage())
    const teams = await view.findByRole('region', { name: '我所在的团队' })
    const link = within(teams).getByText('城市数据实验室').closest('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/teams/citylab/credits')
  })
})
