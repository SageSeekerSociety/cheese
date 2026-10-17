/**
 * 「总览」这一格**容器**（`Overview.vue`）：取数（总览 + 告警两处）、两个下拉写回路由、
 * 点一个能处理的数跳去「题目」那一格，都在这一层；画法在 `OverviewView.vue`（预览站按
 * props 挂着，见 `catalogScenesSpacesDetail.ts` 的 `overview-view`）。
 * 形状照同一目录的 `Learning.spec.ts`。
 */
import type { SpaceAnalyticsAlerts, SpaceAnalyticsOverview } from '@/types/apiSpaces'

import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getAnalyticsOverview: vi.fn(),
  getAnalyticsAlerts: vi.fn(),
  getAnalyticsPublishers: vi.fn(),
}))
vi.mock('@/api/spaces', () => ({ SpacesApi: api }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

import Overview from './Overview.vue'

import i18n from '@/i18n'
import { ANALYTICS_ALERTS } from '@/views/demo/catalogScenesMisc2Fixtures'
import { analyticsOverview, analyticsPublishers } from '@/views/demo/catalogScenesSpacesDetailFixtures'

const OVERVIEW = analyticsOverview()
const PUBLISHER_OPTIONS = analyticsPublishers().map((publisher) => ({
  title: publisher.publisherName,
  value: publisher.publisherId,
}))

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
  props: [
    'publisherId',
    'groupBy',
    'loading',
    'failed',
    'errorDetail',
    'overview',
    'alerts',
    'publishers',
    'publishersLoading',
  ],
  setup(props, { emit }) {
    state = props
    send = emit
    return () => null
  },
})

async function renderPage(query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/manage/analytics', component: Overview },
      {
        path: '/spaces/:spaceId/manage/analytics/tasks',
        name: 'SpacesDetailAnalyticsTasks',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push(`/spaces/8/manage/analytics${query}`)
  await router.isReady()
  render(Overview, { global: { plugins: [router, i18n], stubs: { OverviewView: Probe } } })
  await flushPromises()
  return { router }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.getAnalyticsOverview.mockResolvedValue(OVERVIEW)
  api.getAnalyticsAlerts.mockResolvedValue(ANALYTICS_ALERTS)
  api.getAnalyticsPublishers.mockResolvedValue({ publishers: analyticsPublishers() })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('总览看板容器', () => {
  it('两块数据并发取，任一还没回来就保持 loading，两块都到了才交给画面', async () => {
    const overview = deferred<SpaceAnalyticsOverview>()
    const alerts = deferred<SpaceAnalyticsAlerts>()
    api.getAnalyticsOverview.mockReturnValue(overview.promise)
    api.getAnalyticsAlerts.mockReturnValue(alerts.promise)

    await renderPage()
    expect(api.getAnalyticsOverview).toHaveBeenCalledOnce()
    expect(api.getAnalyticsAlerts).toHaveBeenCalledOnce()
    expect(api.getAnalyticsAlerts).toHaveBeenCalledWith(8)
    expect(state['loading']).toBe(true)

    overview.resolve(OVERVIEW)
    await flushPromises()
    expect(state['loading']).toBe(true)

    alerts.resolve(ANALYTICS_ALERTS)
    await flushPromises()
    expect(state['loading']).toBe(false)
    expect(state['overview']).toEqual(OVERVIEW)
    expect(state['alerts']).toEqual(ANALYTICS_ALERTS)
    expect(state['publishers']).toEqual(PUBLISHER_OPTIONS)
    expect(state['failed']).toBe(false)
  })

  it('两个下拉写回地址并因此重取，地址变了模型跟着变', async () => {
    const { router } = await renderPage()

    send('update:groupBy', 'day')
    await flushPromises()
    expect(router.currentRoute.value.query.groupBy).toBe('day')
    expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(2)
    const [, params] = api.getAnalyticsOverview.mock.calls[1] as [number, Record<string, unknown>]
    expect(params).toMatchObject({ groupBy: 'day' })

    // 出题人那个下拉也要守住写回那一头：只在下面从地址里读它，断掉写回照样是绿的。
    send('update:publisherId', 5)
    await flushPromises()
    expect(router.currentRoute.value.query.publisherId).toBe('5')
    expect(api.getAnalyticsOverview).toHaveBeenCalledTimes(3)
    const [, byPublisher] = api.getAnalyticsOverview.mock.calls[2] as [number, Record<string, unknown>]
    expect(byPublisher).toMatchObject({ publisherId: 5 })

    await router.push({ query: { publisherId: '3' } })
    await flushPromises()
    expect(state['publisherId']).toBe(3)
    const [, after] = api.getAnalyticsOverview.mock.calls[3] as [number, Record<string, unknown>]
    expect(after).toMatchObject({ publisherId: 3 })
  })

  it('点一个能处理的数：跳到「题目」那一格，并把那个条件带上', async () => {
    const { router } = await renderPage('?publisherId=3')

    send('openTasks', { hasPendingReview: true })
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('SpacesDetailAnalyticsTasks')
    expect(router.currentRoute.value.params.spaceId).toBe('8')
    expect(router.currentRoute.value.query.hasPendingReview).toBe('true')
    // 这一格的筛选跟着带过去，跳过去看到的是同一批题。
    expect(router.currentRoute.value.query.publisherId).toBe('3')
  })

  it('读失败：把原因交给画面，两份数据都不换成空的', async () => {
    await renderPage()
    expect(state['overview']).toEqual(OVERVIEW)

    api.getAnalyticsAlerts.mockRejectedValueOnce(new Error('统计接口 500'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    send('load')
    await flushPromises()

    expect(state['failed']).toBe(true)
    expect(state['errorDetail']).toBe('统计接口 500')
    expect(state['overview']).toEqual(OVERVIEW)
    expect(state['alerts']).toEqual(ANALYTICS_ALERTS)
    expect(state['loading']).toBe(false)
  })
})
