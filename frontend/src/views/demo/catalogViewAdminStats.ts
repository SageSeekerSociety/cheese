/**
 * 预览站里后台那一组的后半：统计页那一整屏（`AdminStatsPageView`）。
 *
 * 这一页是「一页一类」：`kind` 决定画哪一屏、页名是什么。它自己是一个 `v-else-if`
 * 的岔口，把七屏看板（`components/admin/dashboard/*.vue`，各自已在 `catalogDashboard.ts`
 * 单列）按类拼起来；取数在路由页 `AdminStatsPage.vue`，这里只吃 props。所以这里摆的
 * 是「同一只壳、换 `kind` 换出不同的一屏」，加上窗口之外的两格 —— 整块读失败，和还没
 * 到货的骨架。
 *
 * 数据复用 `catalogDashboardFixtures.ts` 里那七份（不另抄一份）：条目和别的分册没有
 * 两样（规矩见 `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  DASH_FEEDBACK,
  DASH_PENDING,
  DASH_PERFORMANCE,
  DASH_PIPELINE,
  DASH_PLATFORM,
  DASH_PRODUCT,
  DASH_USAGE,
} from './catalogDashboardFixtures'

import AdminStatsPageView from '@/views/admin/AdminStatsPageView.vue'

/** 画法要 Vuetify、文案要语言包；各屏里的 KPI 卡、对话链接还要一条 route。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 每一屏共用的那半：页头那一行、窗口、时间戳、加载态。各格只换 `kind` 和一屏自己的数据。 */
const base = {
  windowed: true,
  days: 30 as const,
  stamp: '06:13',
  loading: false,
  failed: false,
  error: null,
  pipeline: null,
  product: null,
  feedback: null,
  usage: null,
  platform: null,
  performance: null,
  integrations: null,
  pending: [],
  listLoading: false,
}

export const VIEW_ADMIN_STATS_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-stats-page-view',
    title: 'AdminStatsPageView',
    about:
      '后台统计页：同一只壳（页头 + 窗口切换器），按这一类画哪一屏 —— 平台、性能、交付管线、用量、产品、反馈趋势、集成状态。整块读失败时页头留着、正文换成读失败。',
    file: 'src/views/admin/AdminStatsPageView.vue',
    component: AdminStatsPageView,
    needs: UI_T_R,
    layout: true,
    args: { ...base, kind: 'platform', platform: DASH_PLATFORM },
    states: [
      {
        name: '平台那一屏',
        note: '默认这一类：账号存量与新增、机器台账、健康三格。机器那几行是**存量不是在线数**，这句话写在屏幕上。',
        props: {},
        expect: '账号总数',
      },
      {
        name: '交付管线那一屏',
        note: '换一个 kind 换一屏：五张卡、活四站导轨、「等你处理 / 卡住了」和失败码。四站是不同时刻的通过量，不是漏斗。',
        props: { kind: 'pipeline', platform: null, pipeline: DASH_PIPELINE },
        expect: '卡住的卡',
      },
      {
        name: '用量那一屏',
        note: '窗口内的 token 与调用、最花的项目、成本，额度燃尽那几个名单。「未定价的 token」单独算，不混进总额装成免费。',
        props: { kind: 'usage', platform: null, usage: DASH_USAGE },
        expect: '额度燃尽',
      },
      {
        name: '产品那一屏',
        note: '北极星、递卡走到哪。两条算不出来的带**理由**画（没有数据源），不画一个假的 0。',
        props: { kind: 'product', platform: null, product: DASH_PRODUCT },
        expect: '递卡走到了哪',
      },
      {
        name: '反馈趋势那一屏',
        note: '四栏计数、状态分布、三条线，和右边「需处理」那十行 —— 那一块走的是另一条请求，列表没到货时只有它是骨架。',
        props: { kind: 'feedback', platform: null, feedback: DASH_FEEDBACK, pending: DASH_PENDING },
        expect: '待分诊',
      },
      {
        name: '性能那一屏',
        note: '唯一读进程内存的一屏：这一刻的接口耗时、此刻最慢那条、路由表。「此刻最慢」直接给结论，不让人自己在表里找。',
        props: { kind: 'performance', platform: null, performance: DASH_PERFORMANCE },
        expect: '此刻最慢',
      },
      {
        name: '首次加载',
        note: '这一类还没到货：页头留着（它是这一页的名字，不是数据），正文各块画自己的骨架。',
        props: { platform: null, loading: true },
      },
      {
        name: '整块读失败',
        note: '这一类什么都没拿到、且有一句服务端原话：正文整块换成读失败 —— 页头还在，原话照抄，重试是唯一主操作。',
        props: {
          platform: null,
          failed: true,
          error: 'GET /admin/stats/platform 500：database is unavailable',
        },
        expectSelector: '[role="alert"]',
      },
    ],
  },
]
