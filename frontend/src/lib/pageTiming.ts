// 打开一个项目页时，每个接口在读者自己的浏览器里花了多久。
//
// 后端的 Server-Timing 只说它自己算了多久，看不见读者到部署之间的网络、走的是哪个
// 入口、页面在等谁。这里在一页打开后等一会儿，把这段时间里发出的 /api 请求从浏览器的
// Resource Timing 里取出来（路径里的 id 折成 :id，不带查询串），连同入口主机报上去，
// 平台管理员在 `/admin/page-timings` 读回最近几百份。只报项目里的页，一页一份。
import type { Router } from 'vue-router'

import { reportPageTiming, type RequestTiming } from '@/api/pageTimings'

/** 打开一页后等多久再收：够首屏的请求都回来，又不至于混进后面的轮询。 */
const SETTLE_MS = 8000
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi
const IS_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function fold(path: string): string {
  return path.replace(UUID, ':id').replace(/\/\d+(?=\/|$)/g, '/:n')
}

function apiPath(url: string): string | null {
  try {
    const u = new URL(url, location.origin)
    const at = u.pathname.indexOf('/api/')
    if (at < 0) return null
    return fold(u.pathname.slice(at + 4))
  } catch {
    return null
  }
}

function collect(since: number): RequestTiming[] {
  const out: RequestTiming[] = []
  for (const entry of performance.getEntriesByType('resource') as PerformanceResourceTiming[]) {
    if (entry.startTime < since) continue
    const path = apiPath(entry.name)
    if (!path || path.endsWith('/page-timings')) continue
    out.push({
      path,
      start: Math.round(entry.startTime - since),
      duration: Math.round(entry.duration),
      ttfb: entry.responseStart > 0 ? Math.round(entry.responseStart - entry.startTime) : null,
      bytes: entry.transferSize || 0,
    })
    if (out.length >= 80) break
  }
  return out
}

export function installPageTiming(router: Router): void {
  if (typeof performance === 'undefined' || typeof performance.getEntriesByType !== 'function') return
  performance.setResourceTimingBufferSize?.(2000)
  let first = true
  let pending: ReturnType<typeof setTimeout> | null = null
  router.afterEach((to, _from, failure) => {
    if (failure) return
    const projectId = typeof to.params.projectId === 'string' ? to.params.projectId : null
    const firstLoad = first
    first = false
    if (pending) clearTimeout(pending)
    pending = null
    if (!projectId || !IS_UUID.test(projectId)) return
    const since = firstLoad ? 0 : performance.now()
    const route = fold(to.path)
    pending = setTimeout(() => {
      pending = null
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
      void reportPageTiming(projectId, {
        route,
        host: location.host,
        first_load: firstLoad,
        loaded: firstLoad && nav ? Math.round(nav.loadEventEnd || nav.domContentLoadedEventEnd) : null,
        requests: collect(since),
      }).catch(() => {})
    }, SETTLE_MS)
  })
}
