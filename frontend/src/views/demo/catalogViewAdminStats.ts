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

import { t } from '@/i18n'
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
    get about() {
      return t('catalog.adminStatsPageView.about')
    },
    file: 'src/views/admin/AdminStatsPageView.vue',
    component: AdminStatsPageView,
    needs: UI_T_R,
    layout: true,
    args: { ...base, kind: 'platform', platform: DASH_PLATFORM },
    states: [
      {
        get name() {
          return t('catalog.adminStatsPageView.s1.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s1.note')
        },
        props: {},
        expect: '账号总数',
      },
      {
        get name() {
          return t('catalog.adminStatsPageView.s2.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s2.note')
        },
        props: { kind: 'pipeline', platform: null, pipeline: DASH_PIPELINE },
        expect: '卡住的卡',
      },
      {
        get name() {
          return t('catalog.adminStatsPageView.s3.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s3.note')
        },
        props: { kind: 'usage', platform: null, usage: DASH_USAGE },
        expect: '额度燃尽',
      },
      {
        get name() {
          return t('catalog.adminStatsPageView.s4.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s4.note')
        },
        props: { kind: 'product', platform: null, product: DASH_PRODUCT },
        expect: '递卡走到了哪',
      },
      {
        get name() {
          return t('catalog.adminStatsPageView.s5.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s5.note')
        },
        props: { kind: 'feedback', platform: null, feedback: DASH_FEEDBACK, pending: DASH_PENDING },
        expect: '待分诊',
      },
      {
        get name() {
          return t('catalog.adminStatsPageView.s6.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s6.note')
        },
        props: { kind: 'performance', platform: null, performance: DASH_PERFORMANCE },
        expect: '此刻最慢',
      },
      {
        get name() {
          return t('catalog.adminStatsPageView.s7.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s7.note')
        },
        props: { platform: null, loading: true },
      },
      {
        get name() {
          return t('catalog.adminStatsPageView.s8.name')
        },
        get note() {
          return t('catalog.adminStatsPageView.s8.note')
        },
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
