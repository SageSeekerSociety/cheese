/**
 * 「参与者」这一格**容器**（`Participants.vue`）：取数、导出、五个下拉写回路由都在这一层，
 * 画法在 `ParticipantsView.vue`。画面那一半由预览站按 props 挂着（`catalogScenesSpacesDetail.ts`
 * 的 `participants-view`），容器这一半没有别的用例碰得到 —— 接线断了照样全绿。这一份守的就是
 * 那几根线：进页面取一次、筛选换了重取、画面报「点了导出」真的去拉 CSV。
 *
 * 形状照同一目录的 `Learning.spec.ts`：画面用一个只记 props、只转事件的桩替掉。
 */
import type { SpaceAnalyticsParticipants } from '@/types/apiSpaces'

import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getAnalyticsParticipants: vi.fn(),
  getAnalyticsPublishers: vi.fn(),
}))
vi.mock('@/api/spaces', () => ({ SpacesApi: api }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/services/account', () => ({ default: { accessToken: 'test-token' } }))

import Participants from './Participants.vue'

import i18n from '@/i18n'
import { analyticsParticipants, analyticsPublishers } from '@/views/demo/catalogScenesSpacesDetailFixtures'

const PARTICIPANTS = analyticsParticipants()
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
    'participationApproved',
    'completionStatus',
    'realName',
    'groupBy',
    'loading',
    'failed',
    'errorDetail',
    'participants',
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
    routes: [{ path: '/spaces/:spaceId/manage/analytics/participants', component: Participants }],
  })
  await router.push(`/spaces/8/manage/analytics/participants${query}`)
  await router.isReady()
  render(Participants, { global: { plugins: [router, i18n], stubs: { ParticipantsView: Probe } } })
  await flushPromises()
  return { router }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.getAnalyticsParticipants.mockResolvedValue(PARTICIPANTS)
  api.getAnalyticsPublishers.mockResolvedValue({ publishers: analyticsPublishers() })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('参与者看板容器', () => {
  it('进页面就按这一格的筛选取一次，读回来的数和出题人选项交给画面', async () => {
    await renderPage('?publisherId=3&groupBy=day')

    expect(api.getAnalyticsParticipants).toHaveBeenCalledOnce()
    const [spaceId, params] = api.getAnalyticsParticipants.mock.calls[0] as [number, Record<string, unknown>]
    expect(spaceId).toBe(8)
    expect(params).toMatchObject({ publisherId: 3, groupBy: 'day' })

    expect(state['participants']).toEqual(PARTICIPANTS)
    expect(state['publishers']).toEqual(PUBLISHER_OPTIONS)
    expect(state['loading']).toBe(false)
    expect(state['failed']).toBe(false)
    expect(state['errorDetail']).toBeNull()
  })

  it('读取期间是 loading，画面报「重新取一次」就再取一次', async () => {
    const again = deferred<SpaceAnalyticsParticipants>()
    await renderPage()
    api.getAnalyticsParticipants.mockReturnValueOnce(again.promise)

    send('load')
    await flushPromises()
    expect(state['loading']).toBe(true)

    again.resolve(PARTICIPANTS)
    await flushPromises()
    expect(state['loading']).toBe(false)
    expect(api.getAnalyticsParticipants).toHaveBeenCalledTimes(2)
  })

  it('五个下拉写回地址（并因此重取），地址一变模型跟着变', async () => {
    const { router } = await renderPage()
    expect(api.getAnalyticsParticipants).toHaveBeenCalledTimes(1)

    send('update:groupBy', 'day')
    send('update:realName', 'with')
    await flushPromises()

    expect(router.currentRoute.value.query.groupBy).toBe('day')
    expect(router.currentRoute.value.query.realName).toBe('with')
    expect(api.getAnalyticsParticipants).toHaveBeenCalledTimes(2)
    const [, params] = api.getAnalyticsParticipants.mock.calls[1] as [number, Record<string, unknown>]
    expect(params).toMatchObject({ groupBy: 'day', realName: 'with' })

    await router.push({ query: { publisherId: '3', completionStatus: 'SUCCESS' } })
    await flushPromises()
    expect(state['publisherId']).toBe(3)
    expect(state['completionStatus']).toBe('SUCCESS')
    // 取消筛选要从地址里去掉，不能留一个空串 —— 那会被当成一个筛选条件传下去。
    send('update:publisherId', null)
    send('update:realName', 'all')
    await flushPromises()
    expect(router.currentRoute.value.query.publisherId).toBeUndefined()
    expect(router.currentRoute.value.query.realName).toBeUndefined()
  })

  it('画面报「点了导出」：真的去拉这一格的 CSV，带上当前筛选和登录凭据', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['a,b']) })
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:csv')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await renderPage('?publisherId=3')

    send('export')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as [string, { headers: Record<string, string> }]
    expect(url).toContain('/spaces/8/analytics/participants/export?')
    expect(new URLSearchParams(url.split('?')[1]).get('publisherId')).toBe('3')
    expect(init).toEqual({ headers: { Authorization: 'Bearer test-token' } })
    // 导完按钮要落回去，不然它会一直转。
    expect(state['exporting']).toBe(false)
  })

  it('读失败：把原因交给画面，已经读回来的那一份不动', async () => {
    await renderPage()
    expect(state['participants']).toEqual(PARTICIPANTS)

    api.getAnalyticsParticipants.mockRejectedValueOnce(new Error('统计接口 500'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    send('load')
    await flushPromises()

    expect(state['failed']).toBe(true)
    expect(state['errorDetail']).toBe('统计接口 500')
    expect(state['participants']).toEqual(PARTICIPANTS)
    expect(state['loading']).toBe(false)
  })
})
