/**
 * 市场那一页（`MarketViewView`）和它的节点看板（`NodeBoard`）在预览站里的条目。
 *
 * 规矩见 `catalog.ts`；单独一份是因为 `catalog.ts` 顶着一千行的上限。数据见
 * `catalogMarketFixtures.ts`。两件都只吃 props：取数在 `views/MarketView.vue`（容器）
 * 和 `composables/useMarketNodes.ts`（每 15 秒刷一次节点）。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `MARKET_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { MARKET_NODES, MARKET_NODES_EMPTY, MARKET_POOLS, MARKET_POOLS_EMPTY } from './catalogMarketFixtures'

import NodeBoard from '@/components/NodeBoard.vue'
import MarketViewView from '@/views/MarketViewView.vue'

/** 看板的字都走 `t`（标题、在线 / 离线、云端 / 自有设备），图标和转圈是 Vuetify 的。 */
const UI: CatalogNeed[] = ['vuetify', 'i18n']

export const MARKET_ENTRIES: CatalogEntry[] = [
  {
    id: 'node-board',
    title: 'NodeBoard',
    about: '环境状态看板：这个部署能跑任务的每一台机器，此刻在不在线、没选时落在哪一台。',
    file: 'src/components/NodeBoard.vue',
    component: NodeBoard,
    needs: UI,
    states: [
      {
        name: '有数据',
        note: '在线的亮绿点，离线的整张淡一档；「没有选择时落在这里」只挂在当前默认的那一台上。',
        props: { board: MARKET_NODES, loading: false, error: null },
        expect: '没有选择时落在这里',
      },
      {
        name: '加载中',
        note: '第一次读还没回来：只有页头和一个转圈，右上角的「进行中几轮」等有数再画。',
        props: { board: null, loading: true, error: null },
        expectSelector: '.v-progress-circular',
      },
      {
        name: '一台都没有',
        note: '读回来了但没有节点：卡片区是空的，页头照样报「全平台进行中 0 轮」。',
        props: { board: MARKET_NODES_EMPTY, loading: false, error: null },
        expect: '全平台进行中 0 轮',
      },
      {
        name: '读失败',
        note: '错误原话放在看板自己的位置上，不顶掉页面别的部分。',
        props: { board: null, loading: false, error: '加载环境状态失败：网络错误' },
        expect: '加载环境状态失败：网络错误',
      },
    ],
  },
  {
    id: 'market-view',
    title: 'MarketViewView',
    about:
      '「匹配与资源」页的画面：题目匹配、模型与环境两张页签；后一张上面是环境状态看板，下面是模型和工作电脑的目录。',
    file: 'src/views/MarketViewView.vue',
    component: MarketViewView,
    // 页头是 `AppPage`（它的返回链接要路由）；正文的字走 `useI18n`。
    needs: ['vuetify', 'i18n', 'router'],
    args: { tab: 'pools', pools: MARKET_POOLS, nodes: MARKET_NODES },
    states: [
      {
        name: '有数据',
        note: '模型一组、工作电脑一组；暂未开通的那张淡一档，默认项挂「默认」。',
        props: {},
        expect: 'GPU 云主机',
      },
      {
        name: '加载中',
        note: '目录和看板各自转圈：两样是两个请求，谁先回来谁先画。',
        props: { pools: null, loading: true, nodes: null, nodesLoading: true },
        expectSelector: '.v-progress-circular',
      },
      {
        name: '空',
        note: '目录读回来两组都空、看板一台节点都没有：分组标题照画，下面没有卡片。',
        props: { pools: MARKET_POOLS_EMPTY, nodes: MARKET_NODES_EMPTY },
        expect: '全平台进行中 0 轮',
      },
      {
        name: '读失败',
        note: '目录读失败时原地给一条重试的路（往外 emit retry）；看板失败各报各的。',
        props: { pools: null, error: 'Failed to fetch', nodes: null, nodesError: '加载环境状态失败' },
        expect: '加载市场失败',
      },
      {
        name: '题目匹配页签',
        note: '页签由页面持有（v-model:tab）：停在题目匹配时只有一段说明，看板不画、也不开始轮询。',
        props: { tab: 'tasks' },
        expect: '机构把',
      },
    ],
  },
]
