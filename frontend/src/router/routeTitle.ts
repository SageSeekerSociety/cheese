import type { RouteLocationNormalized, RouteLocationNormalizedLoaded } from 'vue-router'

import { t } from '@/i18n'

/**
 * 由深到浅取第一个有标题的祖先，按当前语言：`/teams/:handle` 自己没有标题，标题在
 * `/teams` 那一层上。
 */
export function routeTitle(route: RouteLocationNormalized | RouteLocationNormalizedLoaded): string {
  for (const record of [...route.matched].reverse()) {
    if (record.meta?.titleKey) return t(record.meta.titleKey)
    if (record.meta?.title) return record.meta.title
  }
  return ''
}
