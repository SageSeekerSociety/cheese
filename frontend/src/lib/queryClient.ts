// 服务器上的数据在前端只有这一份：每一种资源一个 key（`queries/keys.ts`），谁要
// 都从这里读，一次请求大家共用，一处刷新各处一起变。
//
// - 读回来的东西默认 30 秒内算新鲜：这期间再打开同一个页面直接用手上这份，不再问；
//   过了就先画手上这份，后台再问一次（切回看过的页面不出加载状态）。
// - 切回窗口时，过期了的再问一次。轮询由看着它的那一处自己定（`refetchInterval`），
//   标签页在后台时不轮询。
// - 失败不自动重试：这里的读大多有页面上的「重试」，或者下一次轮询本来就会再问。
// - 推送和写操作改的是这份缓存（`invalidateQueries` / `setQueryData`），不是各处的
//   副本。一次标过期会作废正在路上的旧请求再重读，所以晚回来的旧结果盖不掉新的。
//
// 换了人登录（`services/account.ts`）整份清空：这里装的全是上一个人看得到的东西。
//
// 只有这一个实例，读的地方直接把它传给 `useQuery`（第二个参数），不靠组件树注入：
// store 和不在组件里调用的组合函数也是这样读的。
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

/**
 * 改缓存里的一份之前先作废正在路上的那次读：它是改之前发出去的，回来会把刚改的
 * 盖回旧样子。改完通常再标一次过期，让服务器那份最终说了算。手上没有这一份就不改。
 */
export async function patchQuery<T>(queryKey: readonly unknown[], change: (current: T) => T): Promise<void> {
  await queryClient.cancelQueries({ queryKey, exact: true })
  queryClient.setQueryData<T>(queryKey, (current) => (current === undefined ? current : change(current)))
}
