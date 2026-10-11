/**
 * `panels/doc/` 这一册：文档面板拆出来的那些零件 —— 顶栏、浮条、大纲、吐槽栏、
 * AI 队友那几张卡、修改记录、版本对比……一格一格摆出来。
 *
 * 条目按文件规模上限拆成两份（`catalogPanelsDocPartA.ts` / `catalogPanelsDocPartB.ts`），
 * 这里只把两份合起来导出 `DOC_PANEL_ENTRIES`，供 `catalogPanels.ts` spread 进
 * `PANEL_ENTRIES`。两份的样例数据同在 `catalogPanelsDocFixtures.ts`。
 */
import type { CatalogEntry } from './catalog'

import { DOC_PANEL_ENTRIES_A } from './catalogPanelsDocPartA'
import { DOC_PANEL_ENTRIES_B } from './catalogPanelsDocPartB'

export const DOC_PANEL_ENTRIES: CatalogEntry[] = [...DOC_PANEL_ENTRIES_A, ...DOC_PANEL_ENTRIES_B]
