/** A team's credit panel names who changed the team the way the rest of the
 * page names people: `@` and their nickname, or their handle when they have
 * none. */
import type { Component } from 'vue'
import type { CreditAudit, CreditTeamDetail } from '@/lib/adminCredits'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

import AdminCreditTeamPanel from './AdminCreditTeamPanel.vue'

import i18n, { setLocale } from '@/i18n'

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
  rank: 0,
  team_count: 1,
  is_default: true,
}

const TEAM = {
  id: 7,
  name: 'Lab',
  handle: 'lab',
  personal_owner: null,
  personal_owner_nickname: null,
  member_count: 2,
  plan: FREE,
  period: { start: '2026-10-01T00:00:00+00:00', credits_total: 125, credits_used: 0 },
  packs: [],
} as unknown as CreditTeamDetail

function entry(actor_handle: string, actor_name: string | null): CreditAudit {
  return {
    created_at: '2026-10-02T08:00:00+00:00',
    actor_handle,
    actor_name,
    action: 'team.plan',
    target: '7',
    before: { plan_key: 'free' },
    after: { plan_key: 'free' },
  }
}

function mount(history: CreditAudit[]) {
  const Wrapper = {
    components: { AdminCreditTeamPanel },
    setup: () => ({ team: TEAM, plans: [FREE], history }),
    template:
      '<v-app><AdminCreditTeamPanel :model-value="true" :team="team" :plans="plans" :history="history" /></v-app>',
  }
  return render(Wrapper as unknown as Component, {
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeAll(() => {
  setLocale('en')
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(cleanup)

it('names the administrator behind each change by their nickname, or their handle when they have none', async () => {
  const view = mount([entry('grace-h', 'Grace'), entry('ops-2', null)])

  expect(await view.findByText(/^@Grace · /)).toBeTruthy()
  expect(view.getByText(/^@ops-2 · /)).toBeTruthy()
  expect(view.queryByText(/@grace-h/)).toBeNull()
})
