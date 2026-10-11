/**
 * 预览站里「账号那一组整页」：从登录进门到设置密码、补邮箱、改密码的每一屏，加上
 * 404 兜底页和文档站的登录页。
 *
 * 条目按文件规模上限拆成两份（`catalogViewAccountPartA.ts` / `catalogViewAccountPartB.ts`），
 * 这里只把两份合起来导出 `VIEW_ACCOUNT_ENTRIES`，供 `catalogViews.ts` spread 进
 * `VIEW_ENTRIES`。各屏公用数据见 `catalogViewAccountFixtures.ts`。
 */
import type { CatalogEntry } from './catalog'

import { VIEW_ACCOUNT_ENTRIES_A } from './catalogViewAccountPartA'
import { VIEW_ACCOUNT_ENTRIES_B } from './catalogViewAccountPartB'

export const VIEW_ACCOUNT_ENTRIES: CatalogEntry[] = [...VIEW_ACCOUNT_ENTRIES_A, ...VIEW_ACCOUNT_ENTRIES_B]
