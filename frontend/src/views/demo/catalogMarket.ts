/**
 * 市场那一页（`MarketViewView`）在预览站里的条目。
 *
 * 规矩见 `catalog.ts`；单独一份是因为 `catalog.ts` 顶着一千行的上限。目录那份夹具和
 * `SkillMarket` 的条目共用（`catalogSkills.ts` 的 `SKILL_MARKET`）。画面只吃 props：
 * 取数在 `views/MarketView.vue`（容器）。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `MARKET_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry } from './catalog'

import { SKILL_MARKET } from './catalogSkills'

import MarketViewView from '@/views/MarketViewView.vue'

export const MARKET_ENTRIES: CatalogEntry[] = [
  {
    id: 'market-view',
    title: 'MarketViewView',
    about:
      '「市场」页的画面：只放能装的——技能 / 插件 / MCP / Agent 模板的目录。模型和环境是项目的配置，在项目设置里选；题目在空间里发布和应征。',
    file: 'src/views/MarketViewView.vue',
    component: MarketViewView,
    // 页头是 `AppPage`（它的返回链接要路由）；正文的字走 `useI18n`。
    needs: ['vuetify', 'i18n', 'router'],
    args: { market: SKILL_MARKET, marketLoaded: true, deviceId: 'dev1' },
    states: [
      {
        name: '有目录、有机器',
        note: '开页就是技能目录：一条能装的技能一张卡，右下角的 + 能按。',
        props: {},
        expect: '把 PDF 里的文字和表格读出来。',
      },
      {
        name: '没机器',
        note: '目录照看；卡片上方先说一句装要一台机器，+ 是灰的。',
        props: { deviceId: null },
        expectSelector: '.mcard__add[disabled]',
      },
      {
        name: '加载中',
        note: '目录还没回来：技能那一格先说一句在读，「先连接一台机器」那句等读完再说。',
        props: { market: null, marketLoaded: false, marketLoading: true },
        expect: '正在读市场…',
      },
    ],
  },
]
