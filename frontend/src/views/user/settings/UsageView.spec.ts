// 「芝士额度」: what a person sees of their month — when it is used up, when it
// resets, the credits besides the plan's only when there are some, and a way to
// each of their teams' pages.
import type { Component } from 'vue'
import type { CreditUsage } from '@/lib/creditUsage'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import UsageView from './UsageView.vue'

import i18n, { setLocale } from '@/i18n'

function days(): CreditUsage['days'] {
  return Array.from({ length: 31 }, (_, i) => ({
    date: `2026-10-${String(i + 1).padStart(2, '0')}`,
    share: i < 2 ? 0.5 : i < 20 ? 0 : null,
    lines: { collab: i < 2 ? 0.5 : 0, ask: 0, write: 0 },
  }))
}

const PERIOD = { unlimited: false, used_ratio: 0.58, remaining_ratio: 0.42, resets_at: '2026-10-31T16:00:00+00:00' }

function usage(over: Partial<CreditUsage> = {}): CreditUsage {
  return {
    plan: { key: 'free', name: 'Free' },
    period: PERIOD,
    windows: [],
    packs: [],
    days: days(),
    projects: [{ id: 'p1', name: '空气质量看板', share: 1 }],
    lines: { collab: 0.6, ask: 0.3, write: 0.1 },
    teams: [
      {
        id: 3,
        name: '城市数据实验室',
        handle: 'citylab',
        plan: { key: 'free', name: 'Free' },
        unlimited: false,
        remaining_ratio: 0.08,
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

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('芝士额度', () => {
  it('a used-up month says so and when it resets', async () => {
    const view = show(usage({ period: { ...PERIOD, used_ratio: 1, remaining_ratio: 0 } }))

    expect(await view.findAllByText('本月已用完')).not.toHaveLength(0)
    expect(view.getByText(/重置/)).toBeTruthy()
    expect(view.queryByText(/还剩/)).toBeNull()
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

  it("the legend's three lines add up to the month's used share", async () => {
    const view = show(usage())
    const month = await view.findByRole('region', { name: '本月' })
    expect(within(month).getByText('58%')).toBeTruthy()
    // 0.58 × (0.6, 0.3, 0.1)
    expect(within(month).getByText('35%')).toBeTruthy()
    expect(within(month).getByText('17%')).toBeTruthy()
    expect(within(month).getByText('6%')).toBeTruthy()
  })

  it('shows the other credits only when there are some', async () => {
    const without = show(usage())
    await without.findByText('我的项目')
    expect(without.queryByText('其他额度')).toBeNull()
    cleanup()

    const withPack = show(
      usage({
        packs: [
          {
            id: 'g1',
            source: 'admin_grant',
            project_id: null,
            task_id: null,
            project_name: null,
            task_name: null,
            remaining_ratio: 0.7,
            expires_at: null,
          },
        ],
      })
    )
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
