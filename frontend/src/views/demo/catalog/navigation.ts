/**
 * 导航与外壳那一册：一页的外形（`AppPage`）、手机上从底部升起来的一组操作
 * （`MobileActionSheet`）、手机底栏（`BottomAppBar`）、手机顶栏中间那一格
 * （`ScreenHeaderTitle`）。
 *
 * 这几个都长在布局里，样例数据（页签、底栏那几格、动作条那几行动作）在
 * `../catalogFixtures.ts`。
 */
import type { CatalogEntry } from './types'

import { defineComponent, h, markRaw } from 'vue'

import { NAV_ITEMS, SHEET_ACTIONS } from '../catalogFixtures'

import AppPage from '@/components/common/AppPage.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import BottomAppBar from '@/components/common/Navigation/BottomAppBar.vue'
import ScreenHeaderTitle from '@/components/common/Navigation/ScreenHeaderTitle.vue'

export const NAVIGATION_ENTRIES: CatalogEntry[] = [
  {
    id: 'app-page',
    title: 'AppPage',
    about: '一页的外形：一条页头 + 下面唯一会滚的正文。',
    file: 'src/components/common/AppPage.vue',
    component: AppPage,
    needs: ['vuetify', 'router'],
    states: [
      {
        name: '一页',
        note: '页头写的是「这一页是什么」；项目名在侧栏上，这里不写第二遍。',
        props: { title: '项目设置' },
        slot: '正文从这里开始，页头之外只有这一块会滚。',
        expect: '项目设置',
      },
      {
        name: '另一页里的一项',
        note: '给了 parent 的页头写成「成员 / 名字」，前一段点得回去。',
        props: { title: '爱丽丝', parent: { label: '成员', to: { path: '/projects/p1/members' } } },
        slot: '这个人参加过的题、交过的作业。',
        expect: '成员',
      },
      {
        name: '多列的工作面',
        note: 'width="full" 给看板那类要摊开的面（read 是读和填表的那一栏）。',
        props: { title: '数据看板', width: 'full' },
        slot: '四列的工作面。',
        expect: '数据看板',
      },
    ],
  },
  {
    id: 'mobile-action-sheet',
    title: 'MobileActionSheet',
    about: '手机上一组操作从屏幕底部升起来。',
    file: 'src/components/common/MobileActionSheet.vue',
    component: MobileActionSheet,
    needs: ['vuetify', 'router'],
    layout: true,
    teleport: true,
    states: [
      {
        name: '升起来的一组',
        note: '危险操作是 --danger-ink；禁用和转圈的行点不动；有 to 的选完就换页。',
        props: { modelValue: true, title: '话题', actions: SHEET_ACTIONS },
        expect: '删除话题',
      },
      {
        name: '带一段自制抬头',
        note: '默认抬头不够用时用 #header 插槽（一排表情就是这么放进去的）。',
        props: { modelValue: true, title: '话题', actions: SHEET_ACTIONS.slice(0, 2) },
        slot: '一排表情从这里开始',
        expect: '重命名',
      },
    ],
  },
  {
    id: 'bottom-app-bar',
    title: 'BottomAppBar',
    about: '手机上那一排底栏：三格，带角标。',
    file: 'src/components/common/Navigation/BottomAppBar.vue',
    component: BottomAppBar,
    needs: ['vuetify', 'router'],
    layout: true,
    states: [
      {
        name: '三格',
        note: '哪一格亮着由调用方说了算（match 那一条是给「一格底下住着好几条路由」准备的）。',
        props: { items: NAV_ITEMS },
        expect: '待办',
      },
      {
        name: '有一颗只做事、不换页',
        note: '没有 to 的格子（「＋新建项目」那种）点了跑 action，不换页；角标超过 99 就写 99+，读屏从 aria-label 里听到件数。',
        props: {
          items: [
            ...NAV_ITEMS.slice(0, 2).map((item) => ({ ...item, badge: item.key === 'inbox' ? 128 : undefined })),
            { key: 'new', type: 'item', title: '新建项目', icon: 'mdi-plus', action: () => {} },
          ],
        },
        expect: '新建项目',
      },
    ],
  },
  {
    id: 'screen-header-title',
    title: 'ScreenHeaderTitle',
    about: '手机顶栏中间那一格：这一屏交出来的标题行，没交就写路由的标题。',
    file: 'src/components/common/Navigation/ScreenHeaderTitle.vue',
    component: ScreenHeaderTitle,
    needs: ['vuetify'],
    states: [
      {
        name: '没声明',
        note: '这一屏什么也没说（待办、资料库这类）：写路由上的标题。',
        props: { header: null, fallback: '资料库' },
        expect: '资料库',
      },
      {
        name: '这一屏自己的标题行',
        note: '频道页、任务页、频道列表：这一屏把它自己的标题行连同参数交给顶栏画（这里用一行字代替）。',
        props: {
          header: {
            content: { component: markRaw(defineComponent({ setup: () => () => h('span', '频道自己的标题行') })) },
          },
          fallback: '项目工作台',
        },
        expect: '频道自己的标题行',
      },
    ],
  },
]
