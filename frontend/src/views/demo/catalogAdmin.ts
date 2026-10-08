/**
 * 预览站里 admin 那一组（`components/admin/*.vue`）的条目：看板上的 KPI 卡、横向排行、
 * 迷你列表，和「等你处理 / 卡住了」那一列。
 *
 * 四件都只吃 props —— 数据、去向、状态名各只有一处定义（`catalogFixtures.ts` 里的
 * KPI_STATES / BAR_ROWS / NUMBER_ROWS / ACTION_ROWS），组件只负责画，所以这里摆的是
 * 产品里真会走到的那几格：可点的一张与只看不点的一张、值读不到、名字长到装不下、
 * 没有数据、首次加载的骨架。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和
 * `catalogViews.ts`、`catalogAttachments.ts` 同一个理由。这里的 `CatalogEntry`、
 * `CatalogNeed` 是 type-only 引用：`catalog.ts` 反过来要 `ADMIN_ENTRIES` 这个值，
 * 运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { ACTION_ROWS, ADMIN_QUEUE, BAR_ROWS, BAR_ROWS_LONG, KPI_STATES, NUMBER_ROWS } from './catalogFixtures'

import AdminActionList from '@/components/admin/AdminActionList.vue'
import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'

const UI: CatalogNeed[] = ['vuetify']

export const ADMIN_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-kpi-card',
    title: 'AdminKpiCard',
    about: '看板 KPI 行里的一张卡：有没有 to 决定它是不是一个可点的东西。',
    file: 'src/components/admin/AdminKpiCard.vue',
    component: AdminKpiCard,
    // 口径注那一颗（AdminNoteTip）用 useI18n。
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '可点的一张',
        note: '有去向：指针、hover 底色、进 Tab 顺序三样都有，还带着环比和逐日折线。',
        props: KPI_STATES.linked,
        expect: '12,048',
      },
      {
        name: '只看不点的一张',
        note: '没有去向：cursor / hover / Tab 三样一样都不给；口径注（note）这时才生效。',
        props: KPI_STATES.plain,
        expect: '316',
      },
      {
        name: '拿不到值',
        note: 'value 是空串画长破折号，不画 0：「没读到」和「读出来了，是零」必须长得不一样。',
        props: KPI_STATES.empty,
        expect: '—',
      },
      {
        name: '首次加载',
        note: '骨架的形状和真卡完全一样：到货那一刻不重排。',
        props: { label: '待分诊', value: '', loading: true, spark: [] },
        needs: UI,
      },
    ],
  },
  {
    id: 'admin-bar-chart',
    title: 'AdminBarChart',
    about: '看板上的横向排行：名字在左、条在中间、数值在右。',
    file: 'src/components/admin/AdminBarChart.vue',
    component: AdminBarChart,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '一张榜',
        note: '每一行本身就是文字（名字和数值都是真文本，条是装饰），读屏直接读得到。',
        props: { title: '最花 token 的项目', rows: BAR_ROWS },
        expect: '空间协作',
      },
      {
        name: '名字长到装不下',
        note: '名字那一格走省略号、挂 title，数值那两列不被挤走。',
        props: { title: '最花 token 的项目', rows: BAR_ROWS_LONG },
        expect: '一个名字长到会走省略号的项目',
      },
      {
        name: '没有数据',
        note: '空态是「还没读到」，不是画一根 0 的柱。',
        props: { title: '最花 token 的项目', rows: [] },
      },
      {
        name: '首次加载',
        note: '骨架行，行高和真行一样。',
        props: { title: '最花 token 的项目', rows: [], loading: true },
        needs: ['vuetify', 'i18n'],
      },
    ],
  },
  {
    id: 'admin-number-list',
    title: 'AdminNumberList',
    about: '看板右侧的迷你列表：整行一个 <a>，行里没有第二个可聚焦的东西。',
    file: 'src/components/admin/AdminNumberList.vue',
    component: AdminNumberList,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '三行',
        note: '状态名和颜色各只有一处定义（feedbackMeta），这里只画。',
        props: { title: '需处理 · 3', rows: NUMBER_ROWS, moreTo: ADMIN_QUEUE },
        expect: '导出一个月的数据要等四十秒',
      },
      {
        name: '空',
        note: '没有行也没有「查看全部」：这一格就是没东西。',
        props: { title: '需处理 · 0', rows: [] },
      },
      {
        name: '首次加载',
        note: '骨架行，和真行的行高一样（28px 那条行高是这一块立得住的前提）。',
        props: { title: '需处理 · 3', rows: [], loading: true },
        needs: ['vuetify', 'i18n'],
      },
    ],
  },
  {
    id: 'admin-action-list',
    title: 'AdminActionList',
    about: '「等你处理 / 卡住了」那一列：有去向的行整行是目的地，没有的就不装成链接。',
    file: 'src/components/admin/AdminActionList.vue',
    component: AdminActionList,
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '三条',
        note: 'tone 只有 warn / danger 用状态色，其余走中性阶；第二行没有去向，是静态的。',
        props: { title: '等你处理', empty: '今天没有卡住的事', rows: ACTION_ROWS, moreTo: ADMIN_QUEUE },
        expect: '合并 #2100（前端边界闸）',
      },
      {
        name: '空屏',
        note: '空态是一句邀请（「今天没有卡住的事」），不是一句道歉。',
        props: { title: '等你处理', empty: '今天没有卡住的事', rows: [] },
        expect: '今天没有卡住的事',
      },
      {
        name: '首次加载',
        note: '骨架行。',
        props: { title: '等你处理', empty: '今天没有卡住的事', rows: [], loading: true },
        needs: ['vuetify', 'i18n'],
      },
    ],
  },
]
