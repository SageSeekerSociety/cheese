import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/pageTimings', () => ({ reportPageTiming: vi.fn(() => Promise.resolve()) }))

import { installPageTiming } from './pageTiming'

import { reportPageTiming } from '@/api/pageTimings'
import { rememberProject } from '@/lib/addresses'

const PROJECT = '0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0'
const SLUG = 'helper'

type To = {
  path: string
  params: Record<string, string>
  matched: { meta?: { projectFrame?: boolean } }[]
  redirectedFrom?: unknown
}
type Hook = (to: To, from: To, failure?: unknown) => void

function fakeRouter(onNavigation?: () => void) {
  let before: ((to: To) => void) | null = null
  let after: Hook | null = null
  const from: To = { path: '/', params: {}, matched: [] }
  const router = {
    beforeEach: (fn: (to: To) => void) => {
      before = fn
    },
    afterEach: (fn: Hook) => {
      after = fn
    },
    before: (to: To) => {
      before?.(to)
      onNavigation?.()
    },
    after: (to: To) => after?.(to, from, undefined),
    go: (to: To) => {
      router.before(to)
      router.after(to)
    },
  }
  return router
}

function projectPage(path: string): To {
  return { path, params: { projectId: SLUG }, matched: [{ meta: { projectFrame: true } }] }
}

function signedIn() {
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'reader' }))
}

describe('pageTiming', () => {
  let clock = 0

  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(reportPageTiming).mockClear()
    rememberProject(PROJECT, SLUG)
    signedIn()
    clock = 100
    vi.spyOn(performance, 'now').mockImplementation(() => clock)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it("reports a project page's API requests with ids folded, and nothing else", () => {
    vi.spyOn(performance, 'getEntriesByType').mockImplementation((type: string) =>
      type === 'resource'
        ? ([
            {
              name: `${location.origin}/api/projects/${PROJECT}/progress?x=1`,
              startTime: 120,
              duration: 250,
              responseStart: 310,
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
              startTime: 130,
              duration: 5,
              responseStart: 103,
              transferSize: 1,
            },
          ] as unknown as PerformanceEntryList)
        : ([] as unknown as PerformanceEntryList)
    )
    let navigations = 0
    const router = fakeRouter(() => {
      navigations += 1
      if (navigations > 1) clock = 300
    })
    installPageTiming(router as never)
    router.go({ path: '/home', params: {}, matched: [] })
    router.go(projectPage('/projects/helper/overview'))
    vi.advanceTimersByTime(8000)

    expect(reportPageTiming).toHaveBeenCalledTimes(1)
    const [projectId, timing] = vi.mocked(reportPageTiming).mock.calls[0]
    expect(projectId).toBe(PROJECT)
    expect(timing.route).toBe('/projects/:id/overview')
    expect(timing.first_load).toBe(false)
    // 这一跳起头时（100 ms）发出的请求算进来，即使 afterEach 已经走到 300 ms。
    expect(timing.requests).toEqual([
      { path: '/projects/:id/progress', start: 20, duration: 250, ttfb: 190, bytes: 900 },
    ])
  })

  it('collects from zero on the first page load', () => {
    vi.spyOn(performance, 'getEntriesByType').mockImplementation((type: string) =>
      type === 'resource'
        ? ([
            {
              name: `${location.origin}/api/projects/${PROJECT}/overview`,
              startTime: 10,
              duration: 90,
              responseStart: 70,
              transferSize: 500,
            },
          ] as unknown as PerformanceEntryList)
        : ([] as unknown as PerformanceEntryList)
    )
    const router = fakeRouter()
    installPageTiming(router as never)
    router.go(projectPage('/projects/helper/overview'))
    vi.advanceTimersByTime(8000)

    const [, timing] = vi.mocked(reportPageTiming).mock.calls[0]
    expect(timing.first_load).toBe(true)
    expect(timing.requests).toEqual([{ path: '/projects/:id/overview', start: 10, duration: 90, ttfb: 60, bytes: 500 }])
  })

  it('keeps the start of the navigation a redirect came from', () => {
    vi.spyOn(performance, 'getEntriesByType').mockImplementation((type: string) =>
      type === 'resource'
        ? ([
            {
              name: `${location.origin}/api/projects/${PROJECT}/overview`,
              startTime: 120,
              duration: 90,
              responseStart: 170,
              transferSize: 500,
            },
          ] as unknown as PerformanceEntryList)
        : ([] as unknown as PerformanceEntryList)
    )
    const router = fakeRouter()
    installPageTiming(router as never)
    router.go({ path: '/home', params: {}, matched: [] })
    clock = 100
    router.before({ path: `/projects/${PROJECT}/overview`, params: { projectId: PROJECT }, matched: [] })
    clock = 300
    // The redirect target carries `redirectedFrom`; the start stays at the first hop.
    router.after({ ...projectPage('/projects/helper/overview'), redirectedFrom: { path: '/x' } })
    vi.advanceTimersByTime(8000)

    const [, timing] = vi.mocked(reportPageTiming).mock.calls[0]
    expect(timing.route).toBe('/projects/:id/overview')
    expect(timing.requests).toEqual([{ path: '/projects/:id/overview', start: 20, duration: 90, ttfb: 50, bytes: 500 }])
  })

  it('reports nothing for a page outside a project', () => {
    const router = fakeRouter()
    installPageTiming(router as never)
    router.go({ path: '/home', params: {}, matched: [] })
    vi.advanceTimersByTime(8000)
    expect(reportPageTiming).not.toHaveBeenCalled()
  })

  it('reports nothing when signed out', () => {
    localStorage.clear()
    const router = fakeRouter()
    installPageTiming(router as never)
    router.go(projectPage('/projects/helper/overview'))
    vi.advanceTimersByTime(8000)
    expect(reportPageTiming).not.toHaveBeenCalled()
  })

  it('reports nothing for /sites/:projectId, which is not a project frame', () => {
    const router = fakeRouter()
    installPageTiming(router as never)
    router.go({ path: `/sites/${SLUG}`, params: { projectId: PROJECT }, matched: [] })
    vi.advanceTimersByTime(8000)
    expect(reportPageTiming).not.toHaveBeenCalled()
  })
})
