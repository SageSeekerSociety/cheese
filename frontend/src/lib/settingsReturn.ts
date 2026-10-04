// 设置是盖在页面上的一层：关掉它回到打开它之前的那一页。那一页的地址要在离开它
// 的那一刻记下来——等关的时候再看，路由上只剩设置自己。
//
// 在设置里面换页（个人资料 → 设备）不算离开：记下的仍是最初那一页。直接从链接打开
// 设置、之前什么页都没有时，由各处设置自己给一个落脚处（个人设置回首页，空间设置
// 回这个空间的题目列表）。
//
// 从设置里点出去的一页也不算离开——设置里的「市场」就是这种页：它是从这一层点开的
// 下一层，关掉设置该回最初那一页。不这么判的话「设置 → 市场」会把这一页改记成市场，
// 关掉设置就落到市场上，正是人刚要离开的那一页。
import type { RouteLocationNormalized } from 'vue-router'
import type { NavTarget } from '@/lib/navTarget'

import { ref } from 'vue'

const lastPage = ref<string | null>(null)

/** 这一格是设置自己吗（设置页以及它里面的各页）。 */
function inSettings(route: RouteLocationNormalized | undefined): boolean {
  return !!route?.matched?.some((record) => record.meta.settingsOverlay)
}

/** 路由每走一步调一次：走到的是普通页面就记下它。设置自己不算；登录、两步验证
 *  这些不在应用外框里的页（`hideAppBar`）也不算——没登录时点开设置的链接会先去登录，
 *  登录完关掉设置不该回到登录页。 */
export function rememberPageBeforeSettings(to: RouteLocationNormalized, from?: RouteLocationNormalized) {
  if (to.matched.some((record) => record.meta.settingsOverlay || record.meta.hideAppBar)) return
  if (inSettings(from)) return
  lastPage.value = to.fullPath
}

/** 关掉设置去哪：打开之前的那一页，没有就用给的落脚处。 */
export function pageBeforeSettings(fallback: NavTarget): NavTarget {
  return lastPage.value ?? fallback
}
