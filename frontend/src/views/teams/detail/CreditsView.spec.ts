// A team's 「额度」: what its month went to, split into the model calls of its
// projects (协作) and the cloud compute they ran (算力).
import type { Component } from 'vue'
import type { CreditUsage } from '@/lib/creditUsage'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import CreditsView from './CreditsView.vue'

import i18n, { setLocale } from '@/i18n'

const USAGE: CreditUsage = {
  plan: { key: 'free', name: 'Free', unlimited: false, credits_per_period: 500, windows: [], models: [] },
  period: {
    unlimited: false,
    credits_total: 500,
    credits_used: 300,
    used_ratio: 0.6,
    remaining_ratio: 0.4,
    resets_at: '2026-10-31T16:00:00+00:00',
  },
  windows: [],
  packs: [],
  days: Array.from({ length: 31 }, (_, i) => ({
    date: `2026-10-${String(i + 1).padStart(2, '0')}`,
    credits: i === 0 ? 300 : i < 20 ? 0 : null,
    lines: { collab: i === 0 ? 210 : 0, compute: i === 0 ? 90 : 0 },
  })),
  projects: [{ id: 'p1', name: '空气质量看板', credits: 300 }],
  lines: { collab: 210, compute: 90 },
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('团队额度', () => {
  it('splits the month into collaboration and compute, and nothing else', async () => {
    const view = render(CreditsView as unknown as Component, {
      props: { usage: USAGE, loading: false, error: null },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })

    const month = await view.findByRole('region', { name: '本月' })
    expect(within(month).getByText(/协作/).textContent).toMatch(/210/)
    expect(within(month).getByText(/算力/).textContent).toMatch(/90/)
    expect(within(month).queryByText(/问答/)).toBeNull()
    expect(within(month).queryByText(/写作/)).toBeNull()
  })
})

// 个人额度页的「奖励记录」：本月邀请额度已满之后首次采纳的好友，那一行是「+0 点」，
// 旁边要说清楚为什么是 0——不然读起来像发错了。
describe('奖励记录里的 0 点邀请', () => {
  function personal(items: { kind: 'invite' | 'feedback' | 'beta'; credits: number; created_at: string }[]) {
    return render(CreditsView as unknown as Component, {
      props: {
        personal: true,
        teamHandle: 'alice',
        usage: USAGE,
        loading: false,
        error: null,
        rewards: { feedback_count: 0, feedback_credits: 0, invite_credits: 1500, items },
      },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })
  }
  const WHY = '这个月通过邀请得到的额度已满 1,500 点，这位好友首次采纳不再给你额度。'

  it('a 0-credit invitation says why it paid nothing', () => {
    const view = personal([{ kind: 'invite', credits: 0, created_at: '2026-10-20T08:00:00Z' }])
    expect(view.getByText('+0 点')).toBeTruthy()
    expect(view.getByRole('button', { name: WHY })).toBeTruthy()
  })

  it('an invitation that paid carries no such explanation', () => {
    const view = personal([{ kind: 'invite', credits: 150, created_at: '2026-10-19T08:00:00Z' }])
    expect(view.getByText('+150 点')).toBeTruthy()
    expect(view.queryByRole('button', { name: WHY })).toBeNull()
  })
})
