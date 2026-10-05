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
