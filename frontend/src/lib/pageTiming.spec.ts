import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/pageTimings', () => ({ reportPageTiming: vi.fn(() => Promise.resolve()) }))

import { installPageTiming } from './pageTiming'

import { reportPageTiming } from '@/api/pageTimings'

const PROJECT = '0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0'

function fakeRouter() {
  let hook: ((to: unknown, from: unknown, failure?: unknown) => void) | null = null
  return {
    afterEach: (fn: typeof hook) => {
      hook = fn
    },
    go: (to: unknown) => hook?.(to, {}, undefined),
  }
}

describe('pageTiming', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(reportPageTiming).mockClear()
  })
  afterEach(() => vi.useRealTimers())

  it("reports a project page's API requests with ids folded, and nothing else", () => {
    vi.spyOn(performance, 'getEntriesByType').mockImplementation((type: string) =>
      type === 'resource'
        ? ([
            {
              name: `${location.origin}/api/projects/${PROJECT}/progress?x=1`,
              startTime: 10,
              duration: 250,
              responseStart: 200,
              transferSize: 900,
            },
            {
              name: `${location.origin}/assets/app.js`,
              startTime: 5,
              duration: 40,
              responseStart: 30,
              transferSize: 1,
            },
            {
              name: `${location.origin}/api/projects/${PROJECT}/page-timings`,
              startTime: 20,
              duration: 5,
              responseStart: 3,
              transferSize: 1,
            },
          ] as unknown as PerformanceEntryList)
        : ([] as unknown as PerformanceEntryList)
    )
    const router = fakeRouter()
    installPageTiming(router as never)
    router.go({ path: `/projects/${PROJECT}/overview`, params: { projectId: PROJECT } })
    vi.advanceTimersByTime(8000)

    expect(reportPageTiming).toHaveBeenCalledTimes(1)
    const [projectId, timing] = vi.mocked(reportPageTiming).mock.calls[0]
    expect(projectId).toBe(PROJECT)
    expect(timing.route).toBe('/projects/:id/overview')
    expect(timing.requests).toEqual([
      { path: '/projects/:id/progress', start: 10, duration: 250, ttfb: 190, bytes: 900 },
    ])
  })

  it('reports nothing for a page outside a project', () => {
    const router = fakeRouter()
    installPageTiming(router as never)
    router.go({ path: '/home', params: {} })
    vi.advanceTimersByTime(8000)
    expect(reportPageTiming).not.toHaveBeenCalled()
  })
})
