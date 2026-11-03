import type { RouteLocationNormalized, RouteLocationRaw, RouterHistory } from 'vue-router'

import { canonicalAddress } from '@/lib/addresses'

/** 改写浏览器已经退到/前进到的旧地址，只换当前那一格，不在它后面再推一格。 */
export async function canonicalNavigation(
  to: RouteLocationNormalized,
  history: RouterHistory
): Promise<true | RouteLocationRaw> {
  // pop 时历史已经到了目标，push 时还在来处。先记住，别让查地址期间的下一次跳转改掉判断。
  const alreadyThere = history.location === to.fullPath
  const target = await canonicalAddress(to)
  if (target === true || !alreadyThere) return target
  return typeof target === 'string' ? { path: target, replace: true } : { ...target, replace: true }
}
