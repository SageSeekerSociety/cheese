/**
 * 「题目」这一格**容器**（`Tasks.vue`）：取数、导出、几个筛选控件写回路由都在这一层，
 * 画法在 `TasksView.vue`（预览站按 props 挂着，见 `catalogScenesSpacesDetail.ts` 的
 * `analytics-tasks-view`）。形状照同一目录的 `Learning.spec.ts`。
 */
import type { SpaceAnalyticsTask } from '@/types/apiSpaces'

import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getAnalyticsTasks: vi.fn(),
  getAnalyticsPublishers: vi.fn(),
}))
vi.mock('@/api/spaces', () => ({ SpacesApi: api }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/services/account', () => ({ default: { accessToken: 'test-token' } }))

import Tasks from './Tasks.vue'

import i18n from '@/i18n'
import { analyticsPublishers, analyticsTask } from '@/views/demo/catalogScenesSpacesDetailFixtures'

const TASKS = [analyticsTask(), analyticsTask({ taskId: 102, taskName: '二分答案' })]
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
    'sortBy',
    'sortOrder',
    'hasPendingApproval',
    'hasPendingReview',
    'loading',
    'failed',
    'errorDetail',
    'tasks',
    'publishers',
    'publishersLoading',
    'exporting',
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
    routes: [{ path: '/spaces/:spaceId/manage/analytics/tasks', component: Tasks }],
  })
  await router.push(`/spaces/8/manage/analytics/tasks${query}`)
  await router.isReady()
  render(Tasks, { global: { plugins: [router, i18n], stubs: { TasksView: Probe } } })
  await flushPromises()
  return { router }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.getAnalyticsTasks.mockResolvedValue({ tasks: TASKS })
  api.getAnalyticsPublishers.mockResolvedValue({ publishers: analyticsPublishers() })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('题目看板容器', () => {
  it('进页面就取一次，读回来的题目和出题人选项交给画面；排序没写时按创建时间倒序', async () => {
    await renderPage()

    expect(api.getAnalyticsTasks).toHaveBeenCalledOnce()
    const [spaceId, params] = api.getAnalyticsTasks.mock.calls[0] as [number, Record<string, unknown>]
    expect(spaceId).toBe(8)
    expect(params).toMatchObject({ taskApproved: 'APPROVED' })
    // 地址里没写排序就不塞给接口，由后端按自己的默认来；画面上那两个控件摆的是
    // 这一层的默认值（按创建时间倒序）。
    expect(params.sortBy).toBeUndefined()

    expect(state['tasks']).toEqual(TASKS)
    expect(state['publishers']).toEqual(PUBLISHER_OPTIONS)
    expect(state['hasPendingApproval']).toBe(false)
    expect(state['hasPendingReview']).toBe(false)
    expect(state['loading']).toBe(false)
  })

  it('读取期间是 loading，画面报「重新取一次」就再取一次', async () => {
    const again = deferred<{ tasks: SpaceAnalyticsTask[] }>()
    await renderPage()
    api.getAnalyticsTasks.mockReturnValueOnce(again.promise)

    send('load')
    await flushPromises()
    expect(state['loading']).toBe(true)

    again.resolve({ tasks: TASKS })
    await flushPromises()
    expect(state['loading']).toBe(false)
    expect(api.getAnalyticsTasks).toHaveBeenCalledTimes(2)
  })

  it('两个「只看待办」开关点一下就写回地址并重取，再点回去要把条件去掉', async () => {
    const { router } = await renderPage()

    send('togglePendingApproval')
    await flushPromises()
    expect(router.currentRoute.value.query.hasPendingApproval).toBe('true')
    expect(api.getAnalyticsTasks).toHaveBeenCalledTimes(2)

    send('togglePendingReview')
    await flushPromises()
    expect(router.currentRoute.value.query.hasPendingReview).toBe('true')
    const [, params] = api.getAnalyticsTasks.mock.calls[2] as [number, Record<string, unknown>]
    expect(params).toMatchObject({ hasPendingApproval: true, hasPendingReview: true })

    send('togglePendingApproval')
    await flushPromises()
    expect(router.currentRoute.value.query.hasPendingApproval).toBeUndefined()
  })

  it('三个下拉写回地址并因此重取', async () => {
    const { router } = await renderPage()

    send('update:publisherId', 4)
    send('update:sortBy', 'participantCount')
    send('update:sortOrder', 'asc')
    await flushPromises()

    expect(router.currentRoute.value.query.publisherId).toBe('4')
    expect(router.currentRoute.value.query.sortBy).toBe('participantCount')
    expect(router.currentRoute.value.query.sortOrder).toBe('asc')
    const [, params] = api.getAnalyticsTasks.mock.calls[1] as [number, Record<string, unknown>]
    expect(params).toMatchObject({ publisherId: 4, sortBy: 'participantCount', sortOrder: 'asc' })
  })

  it('画面报「点了导出」：按现在这份筛选去拉 CSV', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['a,b']) })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:csv')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await renderPage('?hasPendingReview=true&publisherId=3')

    send('export')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as [string, { headers: Record<string, string> }]
    expect(url).toContain('/spaces/8/analytics/tasks/export?')
    const query = new URLSearchParams(url.split('?')[1])
    expect(query.get('hasPendingReview')).toBe('true')
    expect(query.get('publisherId')).toBe('3')
    expect(init).toEqual({ headers: { Authorization: 'Bearer test-token' } })
    expect(state['exporting']).toBe(false)
  })

  it('读失败：把原因交给画面，已经读回来的那一份不动', async () => {
    await renderPage()
    expect(state['tasks']).toEqual(TASKS)

    api.getAnalyticsTasks.mockRejectedValueOnce(new Error('统计接口 500'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    send('load')
    await flushPromises()

    expect(state['failed']).toBe(true)
    expect(state['errorDetail']).toBe('统计接口 500')
    expect(state['tasks']).toEqual(TASKS)
  })
})
