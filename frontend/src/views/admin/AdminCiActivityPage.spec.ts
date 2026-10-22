import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('@/api', () => ({ request: mocks.request }))

import AdminCiActivityPage from './AdminCiActivityPage.vue'

import i18n, { setLocale } from '@/i18n'
import { CRV_CI_BOARD, CRV_CI_JOBS } from '@/views/demo/catalogViewAdminFixtures'

beforeEach(() => {
  mocks.request.mockReset()
  setLocale('en')
})

function mount() {
  return render(AdminCiActivityPage, { global: { plugins: [i18n, createVuetify({ components, directives })] } })
}

it('replaces a failed window with an error and retries the selected window', async () => {
  let reads = 0
  mocks.request.mockImplementation((url: string) => {
    if (url.includes('/jobs?')) return Promise.resolve({ jobs: [] })
    reads += 1
    if (reads === 2) return Promise.reject(new Error('HTTP 503'))
    return Promise.resolve({
      ...CRV_CI_BOARD,
      days: reads === 1 ? 7 : 1,
      headline: {
        ...CRV_CI_BOARD.headline,
        merge_queue_minutes: { ...CRV_CI_BOARD.headline.merge_queue_minutes, p50: reads === 1 ? 63.2 : 4.2 },
      },
    })
  })
  const page = mount()
  const queueValue = () => page.container.querySelector('.aci__kpis .akpi__num')?.textContent
  await waitFor(() => expect(queueValue()).toBe('63.2 min'))

  await fireEvent.click(page.getByRole('tab', { name: '1 days' }))

  await page.findByText('Could not load the CI data')
  expect(queueValue()).toBeUndefined()
  expect(page.getByRole('tab', { name: '1 days' }).getAttribute('aria-selected')).toBe('true')
  await fireEvent.click(page.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(queueValue()).toBe('4.2 min'))
  expect(page.queryByText('Could not load the CI data')).toBeNull()
  expect(mocks.request.mock.calls.filter(([url]) => url === '/admin/ci-activity?days=1')).toHaveLength(2)
})

it('requests steps for the selected workflow when two jobs have the same name', async () => {
  const workflows = ['.github/workflows/backup-freshness.yml', '.github/workflows/tls-expiry.yml']
  mocks.request.mockImplementation((url: string) => {
    if (url.includes('/jobs?'))
      return Promise.resolve({
        jobs: workflows.map((workflow_path) => ({
          ...CRV_CI_JOBS[0],
          name: 'check',
          workflow: workflow_path,
          workflow_path,
        })),
      })
    if (url.includes('/steps?')) {
      const query = new URL(url, 'https://example.invalid').searchParams
      return Promise.resolve({
        steps: [{ name: query.get('workflow'), number: 1, duration_s: { n: 1, mean: 5, p50: 5, p90: 5, max: 5 } }],
      })
    }
    return Promise.resolve(CRV_CI_BOARD)
  })
  const page = mount()
  const buttons = await page.findAllByRole('button', { name: 'check' })

  await fireEvent.click(buttons[1])

  await waitFor(() => {
    const call = mocks.request.mock.calls.find(([url]) => url.includes('/steps?'))
    expect(new URL(call?.[0] ?? '', 'https://example.invalid').searchParams.get('workflow')).toBe(workflows[1])
  })
  expect(buttons[0].getAttribute('aria-expanded')).toBe('false')
  expect(buttons[1].getAttribute('aria-expanded')).toBe('true')
})
