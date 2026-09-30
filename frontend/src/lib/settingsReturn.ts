// 设置是盖在页面上的一层：关掉它回到打开它之前的那一页。那一页的地址要在离开它
// 的那一刻记下来——等关的时候再看，路由上只剩设置自己。
//
// 在设置里面换页（个人资料 → 设备）不算离开：记下的仍是最初那一页。直接从链接打开
// 设置、之前什么页都没有时，由各处设置自己给一个落脚处（个人设置回首页，空间设置
// 回这个空间的题目列表）。
import type { RouteLocationNormalized } from 'vue-router'
import type { NavTarget } from '@/lib/navTarget'

import { ref } from 'vue'

const lastPage = ref<string | null>(null)

/** 路由每走一步调一次：走到的是普通页面就记下它。 */
export function rememberPageBeforeSettings(to: RouteLocationNormalized) {
  if (to.matched.some((record) => record.meta.settingsOverlay)) return
  lastPage.value = to.fullPath
}

/** 关掉设置去哪：打开之前的那一页，没有就用给的落脚处。 */
export function pageBeforeSettings(fallback: NavTarget): NavTarget {
  return lastPage.value ?? fallback
}
