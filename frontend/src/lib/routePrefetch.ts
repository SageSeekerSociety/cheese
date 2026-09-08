// 鼠标还在路上的时候，把点下去之后要等的那两段先走掉。
//
// 一次首访某个页面是两段串行的等待：先下这个路由的懒加载 chunk（话题页 125 KB），
// chunk 下完才开始拉数据。指针停在一行上到真正按下去之间有一百多毫秒是白等的，
// 这个文件把这两段挪到那段时间里去做。
//
// 它的全部纪律是一句话：**预取是顺手做的事，不是必须做完的事。** 它绝不能抢用户
// 此刻真正在等的那个请求，绝不能因为失败而让任何人看见什么，也绝不能因为「取过一
// 次」而改变界面上的任何状态——尤其是未读红点。
import type { RouteLocationRaw, Router } from 'vue-router'

import { refreshBlockCache } from './blockCache'

/**
 * 指针进来之后要停这么久才算「想点」。
 *
 * 划过整个列表的路上会连着触发十几个 mouseenter，那不是意图，那是路过；等一下再
 * 动手，路过的那些就在这里被自然滤掉了，一个请求都不会发。
 */
export const HOVER_INTENT_MS = 150

export interface HoverTarget {
  /** 解析 `to` 用的 router。只想预热数据时可以不给。 */
  router?: Router
  /** 点下去会去的地方——它那几个懒加载 chunk 会被提前下下来。 */
  to?: RouteLocationRaw
  /** 要预热消息的话题：把它最新那一页取回缓存里。 */
  topicId?: string
}

// 取过就不再取。路由按解析出来的完整路径记，话题按 id 记。
const warmedRoutes = new Set<string>()
const warmedTopics = new Set<string>()

// 指针只有一个，所以待触发的预取也只有一个：进到新的一行就顶掉上一行的，离开就
// 取消。不需要按目标记一堆 timer，那反而会让「路过一整列」留下一串定时器。
let pending: ReturnType<typeof setTimeout> | null = null

/**
 * 省流量模式和慢速网络一律不预取。
 *
 * `navigator.connection` 不是所有浏览器都有；取不到就当作可以预取（这是多数桌面
 * 浏览器的情况，而桌面正是预取有意义的地方）。
 */
function connectionAllows(): boolean {
  const conn = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection
  if (!conn) return true
  if (conn.saveData) return false
  return conn.effectiveType !== 'slow-2g' && conn.effectiveType !== '2g'
}

/**
 * 只在真有一根精确指针的设备上预取。
 *
 * 触屏浏览器会在手指落下时合成一整套 mouseover/mouseenter，于是「手指滑过话题
 * 列表」会变成一串预取——正好是在流量最金贵、CPU 最紧张的那类设备上。没有
 * matchMedia 的环境当作可以预取。
 */
function pointerIsFine(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return true
  try {
    return window.matchMedia('(hover: hover) and (pointer: fine)').matches
  } catch {
    return true
  }
}

// Vue Router 里懒加载路由的 component 就是那个 `() => import(...)` 函数本身，
// 调一次就会去下 chunk。函数式组件也是函数，所以得把它们摘出去——判据抄自
// vue-router 自己区分「异步组件加载器」和「组件」的那几个字段。
function isLazyLoader(component: unknown): component is () => unknown {
  if (typeof component !== 'function') return false
  return !('displayName' in component) && !('props' in component) && !('__vccOpts' in component)
}

function warmRoute(router: Router, to: RouteLocationRaw): void {
  let resolved: ReturnType<Router['resolve']>
  try {
    resolved = router.resolve(to)
  } catch {
    return // 解析不了（比如路由名不存在）就当没这回事
  }
  if (warmedRoutes.has(resolved.fullPath)) return
  warmedRoutes.add(resolved.fullPath)
  for (const record of resolved.matched) {
    for (const component of Object.values(record.components ?? {})) {
      if (!isLazyLoader(component)) continue
      try {
        const loading = component() as Promise<unknown> | unknown
        if (loading instanceof Promise) loading.catch(() => {})
      } catch {
        // 失败就是没预热成，下次点进去照常走一遍，用户看不见任何东西
      }
    }
  }
}

/**
 * 预热话题最新一页消息。
 *
 * 复用 `blockCache` 那条队列，而不是自己再开一条：那条闸一次只放两个请求出去，
 * 另起一套等于把闸开大一倍，也就等于把这里的「顺手」变成了和用户抢。
 *
 * 只写缓存。不碰未读、不标已读、不动当前页面的任何状态。
 */
async function warmTopic(topicId: string): Promise<void> {
  if (warmedTopics.has(topicId)) return
  const blocks = await refreshBlockCache(topicId)
  // 成功了才记住。失败不重试也不留痕——下一次真的把指针停上来，再顺手试一次。
  if (blocks) warmedTopics.add(topicId)
}

/** 指针进入一个可点的东西：等它停住，然后把它要用的东西先取回来。 */
export function prefetchOnHover(target: HoverTarget): void {
  cancelPrefetch()
  if (!pointerIsFine() || !connectionAllows()) return
  pending = setTimeout(() => {
    pending = null
    if (target.router && target.to !== undefined) warmRoute(target.router, target.to)
    if (target.topicId) void warmTopic(target.topicId)
  }, HOVER_INTENT_MS)
}

/** 指针在停住之前就走了：那不是意图，什么都不该发生。 */
export function cancelPrefetch(): void {
  if (pending !== null) {
    clearTimeout(pending)
    pending = null
  }
}
