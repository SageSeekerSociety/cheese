/**
 * The CI activity screen, read from the outside:
 *
 * - a figure with no samples is a dash, never a zero (0 minutes in the queue
 *   would read as a queue that took no time);
 * - while the sweep is still backfilling, the page says how far back the data
 *   reaches, and quotes why the last sweep stopped;
 * - the page says when the board's figures were computed;
 * - picking a runner asks for that runner's jobs, and picking it again lets go;
 * - opening a job shows its steps.
 */
import type { CiBoard } from '@/views/admin/ciActivityApi'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import AdminCiActivityPageView from './AdminCiActivityPageView.vue'

import { CRV_CI_BOARD, CRV_CI_JOBS, CRV_CI_STEPS } from '@/views/demo/catalogViewAdminFixtures'

function mount(props: Record<string, unknown>) {
  const vuetify = createVuetify({ components, directives })
  return render(AdminCiActivityPageView, { props, global: { plugins: [vuetify] } })
}

const EMPTY = { n: 0, mean: null, p50: null, p90: null, max: null }

describe('AdminCiActivityPageView', () => {
  it('shows a dash for a figure with no samples, not a zero', () => {
    const board: CiBoard = {
      ...CRV_CI_BOARD,
      headline: { ...CRV_CI_BOARD.headline, merge_queue_minutes: { ...EMPTY } },
    }
    const { container } = mount({ board, jobs: CRV_CI_JOBS })

    const cards = Array.from(container.querySelectorAll('.aci__kpis > *'))
    const queue = cards.find((card) => card.textContent?.includes('ciActivity.kpi.queue'))
    expect(queue?.textContent).toContain('—')
    expect(queue?.textContent).not.toMatch(/ciActivity\.unit\.minutes \{"n":"0"\}/)
  })

  it('says when the board it shows was computed', () => {
    const board: CiBoard = { ...CRV_CI_BOARD, generated_at: '2026-10-17T22:41:09+00:00' }
    const { getByText } = mount({ board, jobs: CRV_CI_JOBS })

    getByText((text) => text.includes('ciActivity.computedAt') && text.includes('2026-10-17 22:41Z'))
  })

  it('says how far back a backfilling sweep reaches and why it stopped', () => {
    const board: CiBoard = {
      ...CRV_CI_BOARD,
      sweep: {
        at: '2026-10-17T22:35:00+00:00',
        error: 'CiQuotaRefused: GitHub quota spent (HTTP 403)',
        jobs_owed: 4210,
        covered_from: '2026-10-12T04:00:00+00:00',
        complete: false,
      },
    }
    const { getByText } = mount({ board, jobs: CRV_CI_JOBS })

    getByText((text) => text.includes('ciActivity.sweep.backfilling') && text.includes('2026-10-12 04:00Z'))
    getByText((text) => text.includes('GitHub quota spent (HTTP 403)'))
  })

  it('asks for one runner and lets it go on a second click', async () => {
    const view = mount({ board: CRV_CI_BOARD, jobs: CRV_CI_JOBS })

    await fireEvent.click(view.getByRole('button', { name: 'cheese-ci-runner-1' }))
    expect(view.emitted('setRunner')).toEqual([['cheese-ci-runner-1']])

    await view.rerender({ board: CRV_CI_BOARD, jobs: CRV_CI_JOBS, runner: 'cheese-ci-runner-1' })
    await fireEvent.click(view.getByRole('button', { name: 'cheese-ci-runner-1' }))
    expect(view.emitted('setRunner')?.[1]).toEqual([null])
  })

  it('does not present a partial CI history as a complete round count', () => {
    const board: CiBoard = {
      ...CRV_CI_BOARD,
      headline: {
        ...CRV_CI_BOARD.headline,
        rounds: { ...CRV_CI_BOARD.headline.rounds, incomplete_prs: 2, covered_from: '2026-10-10T00:00:00Z' },
      },
    }
    const { getByText } = mount({ board })

    getByText(
      (text) =>
        text.includes('ciActivity.note.roundsIncomplete') &&
        text.includes('2026-10-10 00:00Z') &&
        text.includes('"n":"2"')
    )
  })

  it('shows the steps of the job that was opened', async () => {
    const view = mount({ board: CRV_CI_BOARD, jobs: CRV_CI_JOBS })

    await fireEvent.click(view.getByRole('button', { name: 'e2e / e2e' }))
    expect(view.emitted('setJob')).toEqual([
      [{ name: 'e2e / e2e', workflow_path: '.github/workflows/required-ci.yml' }],
    ])

    await view.rerender({ board: CRV_CI_BOARD, jobs: CRV_CI_JOBS, job: CRV_CI_JOBS[0], steps: CRV_CI_STEPS })
    view.getByText('Run e2e')
  })

  it('omits an unobserved hour and keeps an observed zero', () => {
    const hour = CRV_CI_BOARD.hour_of_day[0]
    const board: CiBoard = {
      ...CRV_CI_BOARD,
      hour_of_day: [
        { ...hour, hour: 0, busy_mean: null },
        { ...hour, hour: 1, busy_mean: 0 },
      ],
    }
    const view = mount({ board })

    expect(view.queryByText('ciActivity.hourOfDay.label {"hour":"00"}')).toBeNull()
    expect(view.getAllByText('ciActivity.hourOfDay.label {"hour":"01"}').length).toBeGreaterThan(0)
  })
})
