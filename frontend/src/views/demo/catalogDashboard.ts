/**
 * 看板那八件在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，八条条目塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogRail.ts` 同一个理由。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `DASHBOARD_ENTRIES`
 * 这个值，运行时不构成循环。
 *
 * 为什么这八件值得一站：它们以前是一个 2880 行的页面里的七个 `v-else-if` 分支，看
 * 其中任何一屏都得先起假后端、把整页拉起来；拆开以后每一屏只吃 props，于是每一屏都
 * 能单独摆在预览站里看。数据见 `catalogDashboardFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  DASH_FEEDBACK,
  DASH_INTEGRATIONS,
  DASH_PENDING,
  DASH_PERFORMANCE,
  DASH_PIPELINE,
  DASH_PLATFORM,
  DASH_PRODUCT,
  DASH_USAGE,
  dashHeaderProps,
} from './catalogDashboardFixtures'

import AdminDashboardFeedback from '@/components/admin/dashboard/AdminDashboardFeedback.vue'
import AdminDashboardHeader from '@/components/admin/dashboard/AdminDashboardHeader.vue'
import AdminDashboardIntegrations from '@/components/admin/dashboard/AdminDashboardIntegrations.vue'
import AdminDashboardPerformance from '@/components/admin/dashboard/AdminDashboardPerformance.vue'
import AdminDashboardPipeline from '@/components/admin/dashboard/AdminDashboardPipeline.vue'
import AdminDashboardPlatform from '@/components/admin/dashboard/AdminDashboardPlatform.vue'
import AdminDashboardProduct from '@/components/admin/dashboard/AdminDashboardProduct.vue'
import AdminDashboardUsage from '@/components/admin/dashboard/AdminDashboardUsage.vue'

/** KPI 卡上的 `to` 是 router-link：不装路由那一格挂载即抛。 */
const UI: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 收货前的骨架。每一屏都一样：`data = null` + `loading`。 */
const LOADING = '首次加载：骨架的形状和真内容一样（到货那一刻不重排），而且一个数都不画。'

export const DASHBOARD_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-dashboard-header',
    title: 'AdminDashboardHeader',
    about: '看板页头右边：窗口 7/30/90 和更新时间。',
    file: 'src/components/admin/dashboard/AdminDashboardHeader.vue',
    component: AdminDashboardHeader,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '有窗口的那一类',
        note: '窗口切换器只在有「过去 N 天」这个说法的分类上出现。',
        props: dashHeaderProps(),
        expect: '30 天',
      },
      {
        name: '没有窗口的那一类',
        note: '集成和性能读的是存量与进程内存，没有「过去 N 天」——切到它们时 7/30/90 那一行整个收起来，不摆一个假窗口。',
        props: dashHeaderProps({ windowed: false }),
        expect: '06:13',
      },
    ],
  },
  {
    id: 'admin-dashboard-pipeline',
    title: 'AdminDashboardPipeline',
    about: '交付管线那一屏：五张卡、活四站导轨、「等你处理 / 卡住了」和失败码。',
    file: 'src/components/admin/dashboard/AdminDashboardPipeline.vue',
    component: AdminDashboardPipeline,
    needs: UI,
    states: [
      {
        name: '一屏交付',
        note: '四站是不同卡在不同时刻的通过量（不是漏斗）；「卡住」那两行进的是停住集合的码，带原话。',
        props: { data: DASH_PIPELINE, loading: false },
        expect: '卡住的卡',
      },
      { name: '首次加载', note: LOADING, props: { data: null, loading: true } },
    ],
  },
  {
    id: 'admin-dashboard-product',
    title: 'AdminDashboardProduct',
    about: '产品健康那一屏：北极星、递卡走到哪、主动消息有用吗，外加两条「今天算不出来」。',
    file: 'src/components/admin/dashboard/AdminDashboardProduct.vue',
    component: AdminDashboardProduct,
    needs: UI,
    states: [
      {
        name: '一屏产品',
        note: '两条算不出来的带**理由**画（没有数据源），不是画一个假的 0。',
        props: { data: DASH_PRODUCT, days: 30, loading: false },
        expect: '递卡走到了哪',
      },
      { name: '首次加载', note: LOADING, props: { data: null, days: 30, loading: true } },
    ],
  },
  {
    id: 'admin-dashboard-integrations',
    title: 'AdminDashboardIntegrations',
    about: '集成健康那一屏：OAuth 凭据、passkey 覆盖、投递账本，和四条「今天查不到」。',
    file: 'src/components/admin/dashboard/AdminDashboardIntegrations.vue',
    component: AdminDashboardIntegrations,
    needs: UI,
    states: [
      {
        name: '一屏集成',
        note: '「还会补发」和「死信」分开画：前者补发扫描还会再试，后者试满次数已放弃 —— 合成一个「未送达」会让有救的和没救的看起来一样。',
        props: { data: DASH_INTEGRATIONS, loading: false },
        expect: 'OAuth 凭据',
      },
      { name: '首次加载', note: LOADING, props: { data: null, loading: true } },
    ],
  },
  {
    id: 'admin-dashboard-feedback',
    title: 'AdminDashboardFeedback',
    about: '反馈那一屏：四栏计数、状态分布、三条线和「需处理」那十行。',
    file: 'src/components/admin/dashboard/AdminDashboardFeedback.vue',
    component: AdminDashboardFeedback,
    needs: UI,
    states: [
      {
        name: '一屏反馈',
        note: '图上点某一天只往上发「点了哪一天」，拼成队列地址是页面的事（路由不进这一件）。',
        props: { data: DASH_FEEDBACK, pending: DASH_PENDING, listLoading: false, days: 30, loading: false },
        expect: '待分诊',
      },
      {
        name: '列表还在路上',
        note: '「需处理」那十行走的是另一条请求（队列），它没到货时只有那一块是骨架 —— KPI 和曲线照画。',
        props: { data: DASH_FEEDBACK, pending: [], listLoading: true, days: 30, loading: false },
        expect: '待分诊',
      },
      {
        name: '首次加载',
        note: LOADING,
        props: { data: null, pending: [], listLoading: false, days: 30, loading: true },
      },
    ],
  },
  {
    id: 'admin-dashboard-usage',
    title: 'AdminDashboardUsage',
    about: '用量那一屏：窗口内的 token 与调用、每天一条线、最花的项目、成本，和额度燃尽那三个名单。',
    file: 'src/components/admin/dashboard/AdminDashboardUsage.vue',
    component: AdminDashboardUsage,
    needs: UI,
    states: [
      {
        name: '一屏用量',
        note: '「未定价的 token」单独算：`cost_usd = 0.0` 是「没有单价」不是「免费」，混进总额会让人以为这个月没花钱。',
        props: { data: DASH_USAGE, days: 30, loading: false },
        expect: '额度燃尽',
      },
      { name: '首次加载', note: LOADING, props: { data: null, days: 30, loading: true } },
    ],
  },
  {
    id: 'admin-dashboard-platform',
    title: 'AdminDashboardPlatform',
    about: '平台那一屏：账号存量与新增、机器台账、健康三格，和配额与缺口那两张分布。',
    file: 'src/components/admin/dashboard/AdminDashboardPlatform.vue',
    component: AdminDashboardPlatform,
    needs: UI,
    states: [
      {
        name: '一屏平台',
        note: '机器那几行是**存量，不是在线数**（在线状态住在进程内存里，库里没有可以查的那一列）——这句话写在屏幕上。',
        props: { data: DASH_PLATFORM, days: 30, loading: false },
        expect: '账号总数',
      },
      { name: '首次加载', note: LOADING, props: { data: null, days: 30, loading: true } },
    ],
  },
  {
    id: 'admin-dashboard-performance',
    title: 'AdminDashboardPerformance',
    about: '性能那一屏：这一刻的接口耗时、此刻最慢那条、路由表和投递积压。',
    file: 'src/components/admin/dashboard/AdminDashboardPerformance.vue',
    component: AdminDashboardPerformance,
    needs: UI,
    states: [
      {
        name: '一屏性能',
        note: '唯一读进程内存的一屏：表按 p95 降序、默认只摆前几条（快路由的长尾是噪音），「此刻最慢」直接给结论。',
        props: { data: DASH_PERFORMANCE, loading: false },
        expect: '此刻最慢',
      },
      { name: '首次加载', note: LOADING, props: { data: null, loading: true } },
    ],
  },
]
