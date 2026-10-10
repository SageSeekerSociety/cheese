import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const reviews = vi.fn()
const review = vi.fn()
vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { reviews: (...args: unknown[]) => reviews(...args), review: (...args: unknown[]) => review(...args) },
}))
// 只换掉 `useI18n`（键名透传，断言直接写键名），`createI18n` 留着 —— `relTime`
// 一路 import 到 i18n 的 barrel，整块换掉 vue-i18n 会让那一层在收集阶段就崩。
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AdminSpacesPage from './AdminSpacesPage.vue'

/**
 * 空间申请（`/admin/spaces`）：待平台管理员过目的开版申请。
 *
 * 这一屏的动作是不可逆的（通过之后空间就公开了），所以测试钉的是三件事：
 *
 * 1. **通过 / 驳回真的打到审核接口上**（驳回还必须带一句理由 —— 申请的人看不到
 *    那句理由之外的任何解释）。
 * 2. **读失败不许画成「暂无申请」**：拿不到数据和一条都没有是两句话。
 * 3. **分页的「后面还有没有」是问出来的，不是猜出来的**：这条路由不给总数，所以
 *    页面多要一条（51）来判断；正好五十条时不该留一个点了没反应的下一页按钮。
 */
function application(over: Record<string, unknown> = {}) {
  return {
    id: 42,
    name: 'Programming course',
    intro: 'Exercises',
    owner: 'teacher',
    reviewStatus: 'PENDING',
    description: '',
    reviewReason: null,
    reviewedBy: null,
    reviewedAt: null,
    createdAt: '2026-09-20T02:40:00+00:00',
    ...over,
  }
}

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
  reviews.mockReset().mockResolvedValue({ data: { items: [application()] } })
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
    // 读不到和一条都没有是两句话：成功那句不能同时出现。
    expect(page.queryByText('spaces.review.empty')).toBeNull()
    // 重试就在原因旁边（这一段自己的位置上）。
    await fireEvent.click(page.getByRole('button', { name: 'spaces.review.retry' }))
    await waitFor(() => expect(reviews).toHaveBeenCalledTimes(2))
  })

  it('一条申请都没有时画空态', async () => {
    reviews.mockResolvedValue({ data: { items: [] } })
    const page = mountPage()
    expect(await page.findByText('spaces.review.empty')).toBeTruthy()
  })
})

describe('space review queue · 列表', () => {
  it('状态页签：三个堆，点了就按那一堆重新取数', async () => {
    const page = mountPage()
    await page.findByText('Programming course')

    const tabs = page.getByRole('tablist', { name: 'spaces.review.status' })
    const buttons = within(tabs).getAllByRole('tab')
    expect(buttons).toHaveLength(3)
    expect(buttons[0].getAttribute('aria-selected')).toBe('true')

    await fireEvent.click(buttons[1])
    // 换了堆就从第一页开始（offset 归零），并且多要一条问「后面还有没有」。
    await waitFor(() => expect(reviews).toHaveBeenLastCalledWith('APPROVED', 0, 51))
  })

  it('intro 与 description 是同一句话时只画一遍', async () => {
    reviews.mockResolvedValue({ data: { items: [application({ intro: 'Exercises', description: 'Exercises' })] } })
    const page = mountPage()
    await page.findByText('Programming course')
    expect(page.getAllByText('Exercises')).toHaveLength(1)
  })

  it('两句不一样时两句都画', async () => {
    reviews.mockResolvedValue({
      data: { items: [application({ intro: 'Exercises', description: 'Weekly exercises, graded' })] },
    })
    const page = mountPage()
    await page.findByText('Programming course')
    expect(page.getByText('Exercises')).toBeTruthy()
    expect(page.getByText('Weekly exercises, graded')).toBeTruthy()
  })

  // 种子里常见的一种：description 就是 intro 开头那句，不判包含的话同一句话会出现
  // 两次（第二遍是前一遍的前缀）。包含时只画长的那句。
  it('一句是另一句的开头时只画长的那句', async () => {
    reviews.mockResolvedValue({
      data: {
        items: [application({ intro: 'Hands-on d3. Anyone may join.', description: 'Hands-on d3.' })],
      },
    })
    const page = mountPage()
    await page.findByText('Programming course')
    expect(page.getByText('Hands-on d3. Anyone may join.')).toBeTruthy()
    expect(page.queryByText('Hands-on d3.')).toBeNull()
  })

  it('已驳回的那一条不再给按钮，并说出驳回原因', async () => {
    reviews.mockResolvedValue({
      data: { items: [application({ reviewStatus: 'REJECTED', reviewReason: 'not a course' })] },
    })
    const page = mountPage()
    await page.findByText('Programming course')
    expect(page.queryByRole('button', { name: 'spaces.review.reject' })).toBeNull()
    expect(page.queryByRole('button', { name: 'spaces.review.approve' })).toBeNull()
    expect(page.getByText(/not a course/)).toBeTruthy()
  })

  // 审核时间和申请时间一样按读者的时钟说成「几小时前」，不是把接口里的 ISO 串原样印出来。
  it('审过的那一条说什么时候审的，不印 ISO 时间串', async () => {
    const reviewedAt = new Date(Date.now() - 3 * 3600_000 - 60_000).toISOString().replace('Z', '+00:00')
    reviews.mockResolvedValue({
      data: { items: [application({ reviewStatus: 'APPROVED', reviewedBy: 'admin', reviewedAt })] },
    })
    const page = mountPage()
    await page.findByText('Programming course')
    expect(page.queryByText(new RegExp(reviewedAt.slice(0, 10)))).toBeNull()
    expect(page.getByText('3 hours ago')).toBeTruthy()
  })
})

describe('space review queue · 分页', () => {
  /** n 条申请（只要 id 不一样，画出来就是 n 行）。 */
  function many(n: number) {
    return Array.from({ length: n }, (_, i) => application({ id: i + 1, name: `Course ${i + 1}` }))
  }

  it('多要一条：回来 51 条就说明后面还有，翻页时 offset 加一页', async () => {
    reviews.mockResolvedValue({ data: { items: many(51) } })
    const page = mountPage()
    await page.findByText('Course 1')

    // 51 条里只画 50 条：多出来的那一条只是判据。
    expect(reviews).toHaveBeenCalledWith('PENDING', 0, 51)
    expect(page.queryByText('Course 51')).toBeNull()

    await fireEvent.click(page.getByRole('button', { name: 'spaces.review.next' }))
    await waitFor(() => expect(reviews).toHaveBeenLastCalledWith('PENDING', 50, 51))
    expect(page.getByRole('button', { name: 'spaces.review.previous' })).toBeTruthy()
  })

  it('正好五十条时不留下一页按钮（后面确实没有了）', async () => {
    reviews.mockResolvedValue({ data: { items: many(50) } })
    const page = mountPage()
    await page.findByText('Course 1')
    // 一页装得下、又没有下一页时不摆一排灰按钮。
    expect(page.queryByRole('button', { name: 'spaces.review.next' })).toBeNull()
    expect(page.queryByRole('button', { name: 'spaces.review.previous' })).toBeNull()
  })
})
