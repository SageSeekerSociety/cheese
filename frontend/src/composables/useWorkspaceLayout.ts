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

/**
 * 桌面窄档的上界：960（mdAndUp）到这个宽度（含）就是「平板横放」那一档。
 *
 * 这一档里常驻二级侧栏（280px）挤掉正文太多，所以它改成可收起的浮层（默认收起），
 * 话题页也从「对话 + 工作面板并排」改成只画对话、面板按需以浮层打开。比它宽就是今
 * 天的样子（侧栏常驻、对话和工作面板可拖 25–80% 分栏）。
 *
 * 这是一条**视口**断点，写不得 CSS 变量（`@media` 里用不了 var），所以它只以常量
 * 存在，由 JS（`matchMedia` / `useDisplay`，见 `useCompactDesktop`）来读。
 */
export const COMPACT_DESKTOP_MAX_WIDTH = 1180

/** 两栏时左边留着话题列表的那几层：列表本身，和从列表打开的房间、任务。 */
const SPLIT_ROUTES: ReadonlySet<RouteRecordNameGeneric> = new Set([
  'workspace-project',
  'workspace-topic',
  'workspace-task',
])

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

/**
 * 现在是不是「桌面窄档」（960–1180）：侧栏可收、房间默认只画对话。
 *
 * `desktop` 就是 Vuetify 的 `mdAndUp`（≥ 960）。上界是
 * `COMPACT_DESKTOP_MAX_WIDTH`。宽度量不出来时（替身 vuetify）按宽档算——那时的
 * 行为就是今天的样子，不额外加一层。
 */
export function compactDesktop(width: number, desktop: boolean): boolean {
  return desktop && width > 0 && width <= COMPACT_DESKTOP_MAX_WIDTH
}

export function useCompactDesktop(): ComputedRef<boolean> {
  const display = useDisplay()
  return computed(() => compactDesktop(display.width?.value ?? 0, display.mdAndUp.value))
}

/**
 * 话题页右侧面板按**主区**宽度（窗口减去侧栏，`.panes` 那一块）分三档：
 *
 * - 至少 `PANEL_OPEN_MIN`：和对话并排，默认开着；
 * - `PANEL_DOCK_MIN` 到它之间：并排，默认收着，点「概览」在右边打开、对话变窄；
 * - 更窄：放不下两栏，面板浮在对话上方，默认收着。
 *
 * 并排时对话至少留 480、面板至少 360，两档的分界就是这么来的。前两档里这个人自己
 * 开或关过一次，以后照他的来；浮层不记，因为开着就挡住对话。
 */
export const PANEL_DOCK_MIN = 840
export const PANEL_OPEN_MIN = 1000

export type PanelMode = 'docked' | 'float'

export function panelMode(mainWidth: number): PanelMode {
  // 还没量出来（第一帧）按并排算：比浮层闪一下再收回去好。
  return mainWidth > 0 && mainWidth < PANEL_DOCK_MIN ? 'float' : 'docked'
}

/** 并排时没人选过，面板开不开。 */
export function panelOpenByDefault(mainWidth: number): boolean {
  return mainWidth === 0 || mainWidth >= PANEL_OPEN_MIN
}
