// 打开一个项目页时，每个接口在读者自己的浏览器里花了多久。
//
// 后端的 Server-Timing 只说它自己算了多久，看不见读者到部署之间的网络、走的是哪个
// 入口、页面在等谁。这里在一页打开后等一会儿，把这段时间里发出的 /api 请求从浏览器的
// Resource Timing 里取出来（路径里的 id 折成 :id，不带查询串），连同入口主机报上去，
// 平台管理员在 `/admin/page-timings` 读回最近几百份。只报项目里的页，一页一份。
import type { RouteLocationNormalized, Router } from 'vue-router'

import { reportPageTiming, type RequestTiming } from '@/api/pageTimings'
import { isUuid, routeIds } from '@/lib/addresses'
import { myId } from '@/me'

/** 打开一页后等多久再收：够首屏的请求都回来，又不至于混进后面的轮询。 */
const SETTLE_MS = 8000
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi

function fold(path: string): string {
  return path.replace(UUID, ':id').replace(/\/\d+(?=\/|$)/g, '/:n')
}

/** 页面的项目：项目框里的路由才报；`routeIds` 把短名换成 UUID。 */
function projectOf(to: RouteLocationNormalized): { id: string; ref: string | null } | null {
  if (!to.matched.some((record) => record.meta?.projectFrame === true)) return null
  const projectId = routeIds(to.params).projectId
  if (!projectId || !isUuid(projectId)) return null
  const ref = typeof to.params.projectId === 'string' ? to.params.projectId : null
  return { id: projectId, ref }
}

/** 短名地址也折成 `/projects/:id/...`，后台按同一份路由归类。 */
function routeOf(path: string, projectRef: string | null): string {
  if (!projectRef || isUuid(projectRef)) return fold(path)
  const slug = `/projects/${projectRef}`
  const at = path.indexOf(slug)
  const end = at + slug.length
  // 只认整整一段：短名 `abc` 不能吃掉 `/projects/abcd` 的前缀。
  if (at < 0 || (end < path.length && path[end] !== '/')) return fold(path)
  return `${fold(path.slice(0, at))}/projects/:id${fold(path.slice(end))}`
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
  if (typeof window === 'undefined' || typeof performance === 'undefined') return
  if (typeof performance.getEntriesByType !== 'function') return
  performance.setResourceTimingBufferSize?.(2000)
  // 这一跳从什么时候开始：要早于路由自己的 prefetch 守卫，它们在这一跳的
  // `afterEach` 之前就把首屏请求发出去了。所以这个钩子必须在 router/index.ts
  // 里紧挨着 createRouter 安装，排在所有守卫前面。
  let navStarted = 0
  let first = true
  let pending: ReturnType<typeof setTimeout> | null = null
  router.beforeEach((to) => {
    // 重定向（短名规范化、旧地址）沿用最初那一跳的时间，别把之前发出的请求丢掉。
    if (!to.redirectedFrom) navStarted = performance.now()
  })
  router.afterEach((to, _from, failure) => {
    if (failure) return
    const firstLoad = first
    first = false
    if (pending) clearTimeout(pending)
    pending = null
    const project = myId() ? projectOf(to) : null
    if (!project) return
    const since = firstLoad ? 0 : navStarted
    const route = routeOf(to.path, project.ref)
    pending = setTimeout(() => {
      pending = null
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
      void reportPageTiming(project.id, {
        route,
        host: location.host,
        first_load: firstLoad,
        loaded: firstLoad && nav ? Math.round(nav.loadEventEnd || nav.domContentLoadedEventEnd) : null,
        requests: collect(since),
      }).catch(() => {})
    }, SETTLE_MS)
  })
}
