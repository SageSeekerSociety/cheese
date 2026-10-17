/**
 * 预览站里「账号设置与项目工作台那一组整页」：账号设置里的七屏（通用、通知、个人资料、
 * 实名信息、密码与安全、模型连接、这台设备），和项目工作台的三屏（全部频道、全部任务、
 * 项目总览）。
 *
 * 这些页面在应用里都由各自的容器取数（`settings/*.vue`、`workspace/*.vue` 那一批），
 * 容器碰接口层，预览站碰不得。好在它们都已经拆成「容器 → 只吃 props 的画面」：这里摆的
 * 正是产品里真会走到的那几格 —— 数据到货 / 首次加载 / 读失败 / 空 / 某一格正忙 / 某一
 * 个弹窗开着。各屏公用的一份数据见 `catalogViewSettingsWorkspaceFixtures.ts`。
 *
 * 条目按文件规模上限拆成三份（`catalogViewSettingsWorkspacePartA/B/C.ts`），这里只把
 * 三份按原来的先后合起来导出 `VIEW_SETTINGS_WORKSPACE_ENTRIES`，供 `catalogViews.ts`
 * spread 进 `VIEW_ENTRIES`。
 */
import type { CatalogEntry } from './catalog'

import { VIEW_SETTINGS_WORKSPACE_ENTRIES_A } from './catalogViewSettingsWorkspacePartA'
import { VIEW_SETTINGS_WORKSPACE_ENTRIES_B } from './catalogViewSettingsWorkspacePartB'
import { VIEW_SETTINGS_WORKSPACE_ENTRIES_C } from './catalogViewSettingsWorkspacePartC'

export const VIEW_SETTINGS_WORKSPACE_ENTRIES: CatalogEntry[] = [
  ...VIEW_SETTINGS_WORKSPACE_ENTRIES_A,
  ...VIEW_SETTINGS_WORKSPACE_ENTRIES_B,
  ...VIEW_SETTINGS_WORKSPACE_ENTRIES_C,
]
