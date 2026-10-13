/**
 * 导出那一件事（`composables/useAnalyticsExport.ts`）：这一格现在筛的是什么就导出什么，
 * 带登录凭据去拉，拉回来存成一个 CSV；拉不到就留一句提示，不当作已经存下。
 *
 * 这一段原来长在 `components/AnalyticsExportButton.vue` 里。拆画面的那一步把它挪到页面
 * 这一层（画面要能只给 props 就挂起来），行为一行没改：地址、凭据、文件名、失败那句
 * 都和原来一样。这一份钉的就是「没改到」。
 */
import type { SpaceAnalyticsQueryState } from '../utils'

import { defineComponent } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ error: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { error: mocks.error } }))
vi.mock('@/services/account', () => ({ default: { accessToken: 'test-token' } }))

import { useAnalyticsExport } from '../composables/useAnalyticsExport'

import i18n, { setLocale } from '@/i18n'

const FILTERS: SpaceAnalyticsQueryState = {
  from: '2026-01-01',
  to: '2026-01-31',
  publisherId: 3,
  taskApproved: 'ALL',
  groupBy: 'week',
  realName: 'all',
}

/** 组合式函数要一个组件的 setup 才取得动；宿主只借它跑一次 setup，不画东西。 */
function mountExport(section: 'tasks' | 'publishers' | 'participants', spaceId = 8) {
  let api!: ReturnType<typeof useAnalyticsExport>
  const host = defineComponent({
    setup() {
      api = useAnalyticsExport(section, spaceId, FILTERS)
      return () => null
    },
  })
  render(host, { global: { plugins: [i18n] } })
  return api
}

/** 存下来的那一次：真的挂了一个 <a download> 上去、点了一下，再收走 blob 地址。 */
function watchDownload() {
  const saved: Array<{ href: string; download: string }> = []
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:csv')
  const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ href: this.href, download: this.download })
  })
  return { saved, revoke, click }
}

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  mocks.error.mockClear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  cleanup()
})

describe('导出分析数据', () => {
  it('把这一格筛的那份交给导出接口，带上登录凭据，成了存成一个 CSV', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['a,b']) })
    vi.stubGlobal('fetch', fetchMock)
    const { saved, revoke } = watchDownload()

    const api = mountExport('tasks')
    await api.run()

    const [url, init] = fetchMock.mock.calls[0] as [string, { headers: Record<string, string> }]
    expect(url).toContain('/spaces/8/analytics/tasks/export?')
    const query = new URLSearchParams(url.split('?')[1])
    expect(query.get('publisherId')).toBe('3')
    expect(query.get('from')).toBeTruthy()
    expect(init).toEqual({ headers: { Authorization: 'Bearer test-token' } })

    expect(saved).toEqual([{ href: 'blob:csv', download: 'space-8-tasks.csv' }])
    expect(revoke).toHaveBeenCalledWith('blob:csv')
    expect(api.exporting.value).toBe(false)
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('导出中报 exporting，回来才落下去', async () => {
    let settle!: (value: unknown) => void
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((resolve) => (settle = resolve)))
    )
    watchDownload()

    const api = mountExport('publishers')
    expect(api.exporting.value).toBe(false)
    const done = api.run()
    expect(api.exporting.value).toBe(true)

    settle({ ok: true, blob: async () => new Blob(['x']) })
    await done
    expect(api.exporting.value).toBe(false)
  })

  it('接口不给文件时说一句，不当作已经存下', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))
    const { saved } = watchDownload()
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {})

    const api = mountExport('participants')
    await api.run()

    expect(saved).toEqual([])
    expect(logged).toHaveBeenCalled()
    expect(mocks.error).toHaveBeenCalledWith('导出失败，稍后重试')
    expect(api.exporting.value).toBe(false)
  })
})
