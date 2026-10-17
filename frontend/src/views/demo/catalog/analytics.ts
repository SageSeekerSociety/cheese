/**
 * 空间分析那一层摆在目录里的两件：数据看板那六格（`AnalyticsNavigationTabs`）、
 * 学习者说的一句话（`LearningQuoteItem`）。
 *
 * 摘录样例在 `../catalogFixtures.ts`。
 */
import type { CatalogEntry } from './types'

import { EXCERPTS } from '../catalogFixtures'

import AnalyticsNavigationTabs from '@/views/spaces/detail/analytics/components/AnalyticsNavigationTabs.vue'
import LearningQuoteItem from '@/views/spaces/detail/analytics/components/LearningQuoteItem.vue'

export const ANALYTICS_ENTRIES: CatalogEntry[] = [
  {
    id: 'analytics-tabs',
    title: 'AnalyticsNavigationTabs',
    about: '数据看板那六格：跳哪儿由挂着它的那棵树说了算。',
    file: 'src/views/spaces/detail/analytics/components/AnalyticsNavigationTabs.vue',
    component: AnalyticsNavigationTabs,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '在空间里',
        note: '路由里有 :spaceId：六格都带着它（换一格不丢当前那一串筛选条件）。',
        props: {},
        expect: '参与者',
      },
      {
        name: '没有路由',
        note: '不装路由也照样画六格（每一格只是一颗 v-tab）——组件不因为缺路由就消失。',
        props: {},
        needs: ['vuetify', 'i18n'],
        expect: '学习',
      },
    ],
  },
  {
    id: 'learning-quote',
    title: 'LearningQuoteItem',
    about: '学习者说的一句话：引文、名字、知识点、时间，和「查看原文」。',
    file: 'src/views/spaces/detail/analytics/components/LearningQuoteItem.vue',
    component: LearningQuoteItem,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '一条摘录',
        note: '「查看原文」带着这条发言的位置去那个话题（buildSourceLink，走 useNavigation）。',
        props: { excerpt: EXCERPTS[0] },
        expect: '因为瑞利散射',
      },
      {
        name: '长引用',
        note: '引文长到好几行：正文自己撑高，右下那颗按钮不掉下去。',
        props: { excerpt: EXCERPTS[1] },
        expect: '至少需要多少',
      },
      {
        name: '批量挑选时',
        note: 'selectable 才画前面那颗勾选框（队列页要一批一批挑）。',
        props: { excerpt: EXCERPTS[0], selectable: true },
        expect: '查看原文',
      },
    ],
  },
]
