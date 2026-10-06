import type { Component } from 'vue'

import { defineAsyncComponent } from 'vue'

// 功能数据页的**前端那一半注册表**：一个功能 = id + 画它的那个组件 + 它那两句文案的
// i18n 键。
//
// 另一半在服务端（`backend/app/domain/feature_stats/registry.py`：id + 标题 + 一句话 +
// 取数函数）。**为什么分成两半**：服务端知道有哪些功能、每个功能的数怎么取；前端知道
// 每个功能长什么样。把 Vue 文件的路径写进服务端的注册表，等于让 Python 记住一个它
// 编译不到、也不会因为改名字而报错的字符串 —— 那是一份一定会漂移的副本。所以组件路径
// 只在这里，数据函数只在那边，两边靠 **id** 对上（就是 URL 里那一段）。
//
// 两半对不上时会怎样，是有意设计过的，不是漏了：
//   * 服务端有、前端没有 → 目录页照样列出这个功能（用服务端那句中文），点进去画
//     「这一页还没做」；不会 404，也不会白屏。
//   * 前端有、服务端没有 → 目录里不会出现，也就没有入口（和没写一样）。
//
// **加一个功能数据页**的步骤写在 `docs/manual/dev/feature-stats.md`。这里的 `id` 必须
// 和 `FEATURE_ID` 一模一样 —— 唯一的耦合点，所以它是文档里最重要的一行。
export interface FeatureView {
  id: string
  /** 目录和页头用的标题。**产品名不翻译**：中英两边都是同一个名字。 */
  titleKey: string
  /** 一句话说明这个功能是干什么的。 */
  summaryKey: string
  view: Component
}

/** 注册表按 id 索引。顺序不在这里定 —— 目录的顺序由服务端那份说了算（它是「有哪
 *  些目的地」的唯一来源），这里只回答「这个 id 画成什么」。 */
const VIEWS: Record<string, FeatureView> = {
  'docs-assistant': {
    id: 'docs-assistant',
    titleKey: 'featureStats.features.docsAssistant.title',
    summaryKey: 'featureStats.features.docsAssistant.summary',
    // 懒加载：功能页是各自一块，谁也不该为另一个功能的图表付首屏的钱。
    view: defineAsyncComponent(() => import('./DocsAssistantPage.vue')),
  },
  'task-naming': {
    id: 'task-naming',
    titleKey: 'featureStats.features.taskNaming.title',
    summaryKey: 'featureStats.features.taskNaming.summary',
    view: defineAsyncComponent(() => import('./TaskNamingPage.vue')),
  },
}

export function findFeatureView(id: string): FeatureView | undefined {
  return VIEWS[id]
}

export function knownFeatureIds(): string[] {
  return Object.keys(VIEWS)
}
