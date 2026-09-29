import type { RouteRecordNameGeneric } from 'vue-router'

import { computed, type ComputedRef } from 'vue'
import { useDisplay } from 'vuetify'

/**
 * 项目工作区按宽度分三种摆法。
 *
 * - `desktop`（≥ 960，Vuetify 的 mdAndUp）：常驻侧栏 + 内容区，房间里对话和工作面板并排。
 * - `split`（768–959，平板竖屏、横过来的大手机）：还是手机外壳（顶栏、底栏、房间的
 *   页签），但话题列表和房间并排成两栏：左边列表，右边当前房间。
 * - `phone`（< 768）：列表和房间各占一整页，页面栈里一层一层走。
 *
 * 两栏只在列表和房间这两层出现（`SPLIT_ROUTES`）。看板、项目文档、设置这些仍是一整页。
 */
export type WorkspaceLayout = 'phone' | 'split' | 'desktop'

/** 从这个宽度起，话题列表和房间并排。 */
export const SPLIT_MIN_WIDTH = 768

/** 两栏时左边话题列表那一栏的宽度。 */
export const SPLIT_LIST_WIDTH = 320

/** 两栏时左边留着话题列表的那几层：列表本身，和从列表打开的房间。 */
const SPLIT_ROUTES: ReadonlySet<RouteRecordNameGeneric> = new Set(['workspace-project', 'workspace-topic'])

export function workspaceLayout(width: number, desktop: boolean): WorkspaceLayout {
  if (desktop) return 'desktop'
  return width >= SPLIT_MIN_WIDTH ? 'split' : 'phone'
}

/** 在两栏摆法下，这一层的左边是不是话题列表。 */
export function showsTopicList(routeName: RouteRecordNameGeneric | null | undefined): boolean {
  return routeName != null && SPLIT_ROUTES.has(routeName)
}

export function useWorkspaceLayout(): ComputedRef<WorkspaceLayout> {
  const display = useDisplay()
  // 只给了 mdAndUp 的替身（不少用例这样模拟 vuetify）量不出宽度，按手机算。
  return computed(() => workspaceLayout(display.width?.value ?? 0, display.mdAndUp.value))
}
