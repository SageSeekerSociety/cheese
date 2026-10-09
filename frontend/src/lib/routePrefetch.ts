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

import { whenIdle } from './idle'

import { prefetchNewestBlocks } from '@/query/blocks'

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

// 取过就不再取，按解析出来的完整路径记。消息手上那份还新鲜就不取（`query/blocks`）。
const warmedRoutes = new Set<string>()

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

// 正在下的预取 chunk。下不下来就是没预热成，但缺块的失败 Vite 照样会广播成
// `vite:preloadError`，而 services/staleBuild.ts 听到它就整页刷新——它得先问这里。
let chunksInFlight = 0

/** 有没有预取的 chunk 还在下。 */
export function prefetchingChunks(): boolean {
  return chunksInFlight > 0
}

/** 一条路由记录上挂着的懒加载 chunk，全下下来。 */
function warmComponents(components: Record<string, unknown> | null | undefined): void {
  for (const component of Object.values(components ?? {})) {
    if (!isLazyLoader(component)) continue
    try {
      const loading = component() as Promise<unknown> | unknown
      if (loading instanceof Promise) {
        chunksInFlight++
        void loading.catch(() => {}).finally(() => chunksInFlight--)
      }
    } catch {
      // 失败就是没预热成，下次点进去照常走一遍，用户看不见任何东西
    }
  }
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
  for (const record of resolved.matched) warmComponents(record.components)
}

/** 按页名预热过的那些页。同一页的不同地址（另一个话题、另一个任务）要的是同一段代码。 */
const warmedPages = new Set<string>()

/**
 * 按**页名**预热一页的代码，不看地址里的参数。
 *
 * 给「这个框架底下那几页」用：框架那一层手上没有每一页的 `topicId` / `kind`，也不该
 * 为了预热编一份出来。同一页的不同地址要的是同一段代码，按名字记一次就够。
 */
function warmPage(router: Router, name: string): void {
  if (warmedPages.has(name)) return
  const record = router.getRoutes().find((candidate) => candidate.name === name)
  if (!record) return
  warmedPages.add(name)
  warmComponents(record.components)
}

/**
 * 预热话题最新一页消息。手上那份还新鲜就不取。
 *
 * 和未读变多时的后台预取排同一条队（`query/blocks`），而不是自己再开一条：那条闸
 * 一次只放两个请求出去，另起一套等于把闸开大一倍，也就等于把这里的「顺手」变成了
 * 和用户抢。
 *
 * 只写缓存。不碰未读、不标已读、不动当前页面的任何状态。失败不重试也不留痕——下一次
 * 真的把指针停上来，再顺手试一次。
 */
function warmTopic(topicId: string): void {
  void prefetchNewestBlocks(topicId)
}

/** 指针进入一个可点的东西：等它停住，然后把它要用的东西先取回来。 */
export function prefetchOnHover(target: HoverTarget): void {
  cancelPrefetch()
  if (!pointerIsFine() || !connectionAllows()) return
  pending = setTimeout(() => {
    pending = null
    if (target.router && target.to !== undefined) warmRoute(target.router, target.to)
    if (target.topicId) warmTopic(target.topicId)
  }, HOVER_INTENT_MS)
}

/**
 * 鼠标左键已经按下去了：意图确定，不再等停住。快手点下去的那一下常常不到 150ms，
 * hover 预取还没来得及起头。只给鼠标用——触屏上每一次手指滑动列表都从一次 pointerdown
 * 开始，那不是意图（调用处按 `pointerType` 过滤）。省流量和慢网照旧让开。
 */
export function prefetchNow(target: HoverTarget): void {
  cancelPrefetch()
  if (!connectionAllows()) return
  if (target.router && target.to !== undefined) warmRoute(target.router, target.to)
  if (target.topicId) warmTopic(target.topicId)
}

/**
 * 首屏画完之后，趁浏览器空着把「下一步多半会去的那几页」的 chunk 先下下来。
 *
 * 和 hover 预取同一个纪律：慢网和省流量不做；一次空闲只热一个目标，不在一帧里连发
 * 一串 import 把主线程占住；人在这期间离开了（返回的取消函数被调用）就停下。
 * 只下代码，不取数据。
 */
export function warmRoutesWhenIdle(router: Router, targets: RouteLocationRaw[]): () => void {
  if (!connectionAllows()) return () => {}
  const queue = [...targets]
  let cancel: () => void = () => {}
  const next = () => {
    const target = queue.shift()
    if (target === undefined) return
    warmRoute(router, target)
    cancel = whenIdle(next)
  }
  cancel = whenIdle(next)
  return () => {
    queue.length = 0
    cancel()
  }
}

/**
 * 同上，但按**页名**点名（见 `warmPage`）。
 *
 * 给一个框架层用它自己那几页：它手上只有页名，没有每一页的地址参数。
 */
export function warmPagesWhenIdle(router: Router, names: readonly string[]): () => void {
  if (!connectionAllows()) return () => {}
  const queue = [...names]
  let cancel: () => void = () => {}
  const next = () => {
    const name = queue.shift()
    if (name === undefined) return
    warmPage(router, name)
    cancel = whenIdle(next)
  }
  cancel = whenIdle(next)
  return () => {
    queue.length = 0
    cancel()
  }
}

/** 指针在停住之前就走了：那不是意图，什么都不该发生。 */
export function cancelPrefetch(): void {
  if (pending !== null) {
    clearTimeout(pending)
    pending = null
  }
}
