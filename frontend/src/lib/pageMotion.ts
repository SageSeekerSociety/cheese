import type { RouteLocationNormalized, RouteLocationNormalizedLoaded, Router } from 'vue-router'

/**
 * 手机上换页时新页从哪边进来。
 *
 * - `forward`：往里走了一层（话题列表 → 话题、项目页 → 它的子页），新页从右边进来；
 * - `back`：退回上一层，新页从左边进来；
 * - `fade`：平级切换（底栏换一格、首页那几段、同一层换一个话题），只淡入；
 * - `null`：不动（第一次打开、只改了 query 或 hash——那是页内的事，比如切页签）。
 *
 * 「一层」按路由自己声明的上一层算（`meta.backTo`，顶栏那颗 ← 读的也是它），不按
 * 浏览器历史：点 ← 回上一层在历史里是一次前进，但人看到的是退回去。深度就是顺着
 * backTo 走到底要几步；一级目的地（底栏那三格落到的页面）没有 backTo，深度 0。
 */
export type PageMotion = 'forward' | 'back' | 'fade' | null

type Location = Pick<RouteLocationNormalized, 'path' | 'meta' | 'params' | 'matched'>

/** 顺着 backTo 往上走几步到顶。解析不了（缺参数、名字不存在）就停在那儿。 */
export function stackDepth(route: Location, router: Router): number {
  let depth = 0
  let parent = route.meta.backTo
  const seen = new Set<string>()
  while (typeof parent === 'string' && !seen.has(parent) && depth < 10) {
    seen.add(parent)
    depth += 1
    try {
      // 参数从当前地址里取、只取上一层用得上的那几个（和 ParentBackButton 同一种解析）：
      // 直接把整份 params 塞进去，vue-router 会为多出来的那几个报警告。
      parent = router.resolve({ name: parent }, route as RouteLocationNormalizedLoaded).meta.backTo
    } catch {
      break
    }
  }
  return depth
}

export function pageMotion(to: Location, from: Location, router: Router): PageMotion {
  if (!from.matched.length) return null
  if (to.path === from.path) return null
  const deeper = stackDepth(to, router) - stackDepth(from, router)
  if (deeper > 0) return 'forward'
  if (deeper < 0) return 'back'
  return 'fade'
}
