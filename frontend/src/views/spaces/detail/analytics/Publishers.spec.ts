/**
 * 「出题人」这一格**容器**（`Publishers.vue`）：取数、导出、两个排序下拉写回路由都在这一层，
 * 画法在 `PublishersView.vue`（预览站按 props 挂着，见 `catalogScenesSpacesDetail.ts`）。
 * 形状照同一目录的 `Learning.spec.ts`：画面用一个只记 props、只转事件的桩替掉。
 */
import type { SpaceAnalyticsPublisherMetrics } from '@/types/apiSpaces'

import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getAnalyticsPublishers: vi.fn() }))
vi.mock('@/api/spaces', () => ({ SpacesApi: api }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/services/account', () => ({ default: { accessToken: 'test-token' } }))

import Publishers from './Publishers.vue'

import i18n from '@/i18n'
import { analyticsPublishers } from '@/views/demo/catalogScenesSpacesDetailFixtures'

const PUBLISHERS = analyticsPublishers()

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
const flushPromises = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

let state: Record<string, unknown>
let send: (event: string, ...args: unknown[]) => void
const Probe = defineComponent({
  props: ['sortBy', 'sortOrder', 'loading', 'failed', 'errorDetail', 'publishers', 'exporting'],
  setup(props, { emit }) {
    state = props
    send = emit
    return () => null
  },
})

async function renderPage(query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId/manage/analytics/publishers', component: Publishers }],
  })
  await router.push(`/spaces/8/manage/analytics/publishers${query}`)
  await router.isReady()
  render(Publishers, { global: { plugins: [router, i18n], stubs: { PublishersView: Probe } } })
  await flushPromises()
  return { router }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.getAnalyticsPublishers.mockResolvedValue({ publishers: PUBLISHERS })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('出题人看板容器', () => {
  it('进页面就取一次，读回来的明细交给画面；排序没写时按题数从多到少', async () => {
    await renderPage()

    expect(api.getAnalyticsPublishers).toHaveBeenCalledOnce()
    const [spaceId, params] = api.getAnalyticsPublishers.mock.calls[0] as [number, Record<string, unknown>]
    expect(spaceId).toBe(8)
    expect(params).toMatchObject({ taskApproved: 'APPROVED' })
    // 地址里没写排序就不塞给接口，由后端按自己的默认来；画面上那两个控件摆的是
    // 这一层的默认值（按题数从多到少）。
    expect(params.sortBy).toBeUndefined()

    expect(state['publishers']).toEqual(PUBLISHERS)
    expect(state['sortBy']).toBe('taskCount')
    expect(state['sortOrder']).toBe('desc')
    expect(state['loading']).toBe(false)
    expect(state['failed']).toBe(false)
  })

  it('读取期间是 loading，画面报「重新取一次」就再取一次', async () => {
    const again = deferred<{ publishers: SpaceAnalyticsPublisherMetrics[] }>()
    await renderPage()
    api.getAnalyticsPublishers.mockReturnValueOnce(again.promise)

    send('load')
    await flushPromises()
    expect(state['loading']).toBe(true)

    again.resolve({ publishers: PUBLISHERS })
    await flushPromises()
    expect(state['loading']).toBe(false)
    expect(api.getAnalyticsPublishers).toHaveBeenCalledTimes(2)
  })

  it('改排序写回地址并因此重取，地址变了模型跟着变', async () => {
    const { router } = await renderPage()

    send('update:sortBy', 'successRate')
    send('update:sortOrder', 'asc')
    await flushPromises()

    expect(router.currentRoute.value.query.sortBy).toBe('successRate')
    expect(router.currentRoute.value.query.sortOrder).toBe('asc')
    expect(api.getAnalyticsPublishers).toHaveBeenCalledTimes(2)
    const [, params] = api.getAnalyticsPublishers.mock.calls[1] as [number, Record<string, unknown>]
    expect(params).toMatchObject({ sortBy: 'successRate', sortOrder: 'asc' })

    await router.push({ query: { sortBy: 'participantCount', sortOrder: 'desc' } })
    await flushPromises()
    expect(state['sortBy']).toBe('participantCount')
    expect(state['sortOrder']).toBe('desc')
  })

  it('画面报「点了导出」：按现在这份排序去拉 CSV', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['a,b']) })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:csv')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await renderPage('?sortBy=successRate&sortOrder=asc')

    send('export')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('/spaces/8/analytics/publishers/export?')
    const query = new URLSearchParams(url.split('?')[1])
    expect(query.get('sortBy')).toBe('successRate')
    expect(query.get('sortOrder')).toBe('asc')
    expect(state['exporting']).toBe(false)
  })

  it('读失败：把原因交给画面，已经读回来的那一份不动', async () => {
    await renderPage()
    expect(state['publishers']).toEqual(PUBLISHERS)

    api.getAnalyticsPublishers.mockRejectedValueOnce(new Error('统计接口 500'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    send('load')
    await flushPromises()

    expect(state['failed']).toBe(true)
    expect(state['errorDetail']).toBe('统计接口 500')
    expect(state['publishers']).toEqual(PUBLISHERS)
    expect(state['loading']).toBe(false)
  })
})
