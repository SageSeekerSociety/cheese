// 服务器上的数据在前端只有这一份：每一种资源一个 key（`query/keys.ts`），谁要
// 都从这里读，一次请求大家共用，一处刷新各处一起变。
//
// - 读回来的东西默认 30 秒内算新鲜：这期间再打开同一个页面直接用手上这份，不再问；
//   过了就先画手上这份，后台再问一次（切回看过的页面不出加载状态）。
// - 切回窗口时，过期了的再问一次。别处的变化由推送叫它重读：房间里的（`query/changes`）、
//   项目框架的（`query/projectFeed`）。
// - 失败不自动重试：这里的读大多有页面上的「重试」，或者下一次推送、切回窗口本来就会再问。
// - 推送和写操作改的是这份缓存（`invalidateQueries` / `setQueryData`），不是各处的
//   副本。一次标过期会作废正在路上的旧请求再重读，所以晚回来的旧结果盖不掉新的。
//
// 换了人登录（`services/account.ts`）整份清空：这里装的全是上一个人看得到的东西。
//
// 只有这一个实例，读的地方直接把它传给 `useQuery`（第二个参数），不靠组件树注入：
// store 和不在组件里调用的组合函数也是这样读的。
import type { InvalidateQueryFilters, Query } from '@tanstack/vue-query'

import { QueryClient } from '@tanstack/vue-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 30 * 60_000,
      retry: false,
      refetchOnWindowFocus: true,
    },
    mutations: { retry: false },
  },
})
// 听窗口回到前台、网络恢复：「切回窗口时过期了的再问一次」靠它。
queryClient.mount()

// Dev-only observability hook: probe scripts (scripts/probe_flash.py) read the
// REAL cache instance — a dynamic import from the console/probe can resolve to a
// second module instance under Vite HMR, which lies.
declare global {
  interface Window {
    __queryClient?: QueryClient
  }
}
if (import.meta.env.DEV) window.__queryClient = queryClient

/**
 * 改缓存里的一份之前先作废正在路上的那次读：它是改之前发出去的，回来会把刚改的
 * 盖回旧样子。改完通常再标一次过期，让服务器那份最终说了算。手上没有这一份就不改。
 */
export async function patchQuery<T>(queryKey: readonly unknown[], change: (current: T) => T): Promise<void> {
  await queryClient.cancelQueries({ queryKey, exact: true })
  queryClient.setQueryData<T>(queryKey, (current) => (current === undefined ? current : change(current)))
}

/** 这一份此刻没有在读了（读完、失败、被作废）。 */
export function settled(query: Query): Promise<void> {
  if (query.state.fetchStatus !== 'fetching') return Promise.resolve()
  return new Promise((resolve) => {
    const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
      if (event.query !== query || query.state.fetchStatus === 'fetching') return
      unsubscribe()
      resolve()
    })
  })
}

// 经这里发起、还没读完的（标过期之后的重读要等下一拍才真的发出去，这期间
// `fetchStatus` 还是 idle，所以自己记着），和排在它后面的那一次。
const running = new Map<string, Promise<void>>()
const queued = new Map<string, Promise<void>>()

function start(query: Query): Promise<void> {
  const hash = query.queryHash
  // 不作废别人刚发出的读：走到这里时它不在读，之后才发出去的读都在这次变化之后。
  const read = queryClient
    .invalidateQueries({ queryKey: query.queryKey, exact: true }, { cancelRefetch: false })
    .finally(() => {
      if (running.get(hash) === read) running.delete(hash)
    })
  running.set(hash, read)
  return read
}

function refreshOne(query: Query): Promise<void> {
  const hash = query.queryHash
  const waiting = queued.get(hash)
  if (waiting) return waiting
  const busy = running.get(hash) ?? (query.state.fetchStatus === 'fetching' ? settled(query) : null)
  if (!busy) return start(query)
  const after = busy
    .catch(() => undefined)
    .then(() => {
      queued.delete(hash)
      return start(query)
    })
  queued.set(hash, after)
  return after
}

/**
 * 「这几份变了，再读一次」（推送说变了、轮到该刷新了）。正在读的那一次是变之前发出去的，
 * 不能拿它的回答；但也不作废它另起一次 —— 那样一串连着来的「变了」就是一串整份重读。
 * 等它回来再读一次，这期间再要的都跟着那一次：同一份同一时刻最多一个请求在路上、一个
 * 排在后面。没人在看的那几份只标过期，下次有人看时再读。
 *
 * 本地刚写过的不走这里：那是 `patchQuery`，改之前发出去的读直接作废。
 */
export function refreshQueries(filters: InvalidateQueryFilters): Promise<void> {
  return Promise.all(queryClient.getQueryCache().findAll(filters).map(refreshOne)).then(() => undefined)
}
