import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const reviews = vi.fn()
const review = vi.fn()
vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { reviews: (...args: unknown[]) => reviews(...args), review: (...args: unknown[]) => review(...args) },
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

import AdminSpacesPage from './AdminSpacesPage.vue'

function mountPage() {
  return render(AdminSpacesPage, { global: { plugins: [createVuetify({ components, directives })] } })
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
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
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
  reviews.mockReset().mockResolvedValue({
    data: {
      items: [{ id: 42, name: 'Programming course', intro: 'Exercises', owner: 'teacher', reviewStatus: 'PENDING' }],
    },
  })
  review.mockReset().mockResolvedValue({ data: {} })
})
afterEach(cleanup)

describe('space review queue', () => {
  it('approves an application and refreshes the queue', async () => {
    const page = mountPage()
    await page.findByText('Programming course')
    await fireEvent.click(page.getByRole('button', { name: 'spaces.review.approve' }))
    await waitFor(() => expect(review).toHaveBeenCalledWith(42, true, ''))
    expect(reviews).toHaveBeenCalledTimes(2)
  })

  it('requires a rejection reason and sends it to the review endpoint', async () => {
    const page = mountPage()
    await page.findByText('Programming course')
    await fireEvent.click(page.getByRole('button', { name: 'spaces.review.reject' }))
    const textarea = await page.findByLabelText('spaces.review.reason')
    const buttons = page.getAllByRole('button', { name: 'spaces.review.reject' })
    const submit = buttons[buttons.length - 1]
    expect(submit.hasAttribute('disabled')).toBe(true)
    await fireEvent.update(textarea, 'Please describe the course')
    await fireEvent.click(submit)
    await waitFor(() => expect(review).toHaveBeenCalledWith(42, false, 'Please describe the course'))
  })

  it('shows a failed queue request instead of an empty queue', async () => {
    reviews.mockRejectedValue(new Error('Forbidden'))
    const page = mountPage()
    expect(await page.findByText('spaces.review.loadFailed')).toBeTruthy()
    expect(page.queryByText('spaces.review.empty')).toBeNull()
  })
})
