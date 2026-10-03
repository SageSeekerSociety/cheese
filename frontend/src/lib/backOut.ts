// 「离开这一层」该走哪一步，收在这里，免得每处各写一份、各写错一半。
//
// 两件事长得像，判据不一样：
//
//  - `closeOverlay`：离开一层，而**去哪是定的**——关掉盖在页面上的那一层（设置、
//    资料库里正看着的那一份），或者离开一个按钮上写着地名的地方（反馈详情回中心）。
//    身后正是要去的那一页就退一格，真把它弹掉；否则把落脚处 `replace` 上去。不能用
//    `push`：那会在身后留下一条和当前地址几乎一样的记录，下一次 ← 又落回刚离开的那
//    一层（关掉设置再按 ←，设置又开了）。
//
//  - `stepBack`：不关心来路是谁，只知道「往回走」是对的（反馈提交页：取消就是别填了）。
//    身后有应用内来路就退一格；没有（贴链接冷开）就 `replace` 到落脚处。不能直接
//    `router.back()`：深链打开时那是把整个应用退出去，而人以为自己按的是「回去」。
import type { RouteLocationRaw, Router } from 'vue-router'

/** 身后那一格的完整地址；冷开（身后是别的站点、或者根本没有）时是 `null`。
 *  读不到就是没有 —— 单测里那个假 router 没有 `options`，这里不该因此抛出去。 */
function historyBack(router: Router): string | null {
  const state = (router.options as { history?: { state?: { back?: unknown } } } | undefined)?.history?.state
  const back = state?.back
  return typeof back === 'string' && back ? back : null
}

/** 落脚处的完整地址；这条路不认识（改了名、参数对不上）时是 `null`。 */
function resolvedPath(router: Router, to: RouteLocationRaw): string | null {
  try {
    return router.resolve(to).fullPath
  } catch {
    return null
  }
}

/** 离开一层、去一个**定好的**地方：身后正是那一页就退一格，否则把落脚处换上去。
 *  去向定不定是它与 `stepBack` 的唯一区别 —— 按钮上写着地名的地方用这个。 */
export function closeOverlay(router: Router, behind: RouteLocationRaw): void {
  const path = resolvedPath(router, behind)
  if (path !== null && historyBack(router) === path) {
    router.back()
    return
  }
  void router.replace(behind)
}

/** 往回走一步：身后有应用内来路就退一格，没有就换到落脚处，而不是把人踢出应用。 */
export function stepBack(router: Router, fallback: RouteLocationRaw): void {
  if (historyBack(router)) {
    router.back()
    return
  }
  void router.replace(fallback)
}
