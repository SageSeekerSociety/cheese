/**
 * 预览站里「第三批拆出来的半张画面」：管理员看板告警、后台成员管理。
 *
 * 这几屏在应用里由各自的路由页取数（`spaces/detail/analytics/Alerts.vue`、
 * `admin/AdminMembersPage.vue`），路由页碰接口层，预览站
 * 碰不得。好在它们都已经拆成「路由页 → 只吃 props 的画面」：这里展示的正是产品里真会
 * 走到的那几格 —— 数据到货 / 首次加载 / 读失败 / 没权限 / 空 / 加删的提示与对话框。
 * 各屏公用的一份数据见 `catalogScenesMisc2Fixtures.ts`。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的
 * `CatalogEntry`、`CatalogNeed` 是 type-only 引用：`catalog.ts` 反过来要分册的值，
 * 运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { ANALYTICS_ALERTS, MEMBERS_CANDIDATES, MEMBERS_ROSTER } from './catalogScenesMisc2Fixtures'

import { t } from '@/i18n'
import AdminMembersPageView from '@/views/admin/AdminMembersPageView.vue'
import AlertsView from '@/views/spaces/detail/analytics/AlertsView.vue'

/** 画法要 Vuetify、文案要语言包；成员管理里按名字指路的去处（用户卡片）要一条 route。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const SCENES_MISC2_ENTRIES: CatalogEntry[] = [
  {
    id: 'alerts-view',
    title: 'AlertsView',
    get about() {
      return t('catalog.alertsView.about')
    },
    file: 'src/views/spaces/detail/analytics/AlertsView.vue',
    component: AlertsView,
    needs: UI_T_R,
    args: { loading: false, failed: false, errorDetail: null, alerts: ANALYTICS_ALERTS },
    states: [
      {
        get name() {
          return t('catalog.alertsView.s1.name')
        },
        get note() {
          return t('catalog.alertsView.s1.note')
        },
        props: {},
        expect: '待审核题目',
      },
      {
        get name() {
          return t('catalog.alertsView.s2.name')
        },
        get note() {
          return t('catalog.alertsView.s2.note')
        },
        props: { loading: true, alerts: null },
        expectSelector: '.v-progress-linear',
      },
      {
        get name() {
          return t('catalog.alertsView.s3.name')
        },
        get note() {
          return t('catalog.alertsView.s3.note')
        },
        props: { failed: true, errorDetail: '告警接口超时', alerts: null },
        expect: '告警接口超时',
      },
      {
        get name() {
          return t('catalog.alertsView.s4.name')
        },
        get note() {
          return t('catalog.alertsView.s4.note')
        },
        props: { alerts: null },
        expect: '暂无告警',
      },
    ],
  },
  {
    id: 'admin-members-page-view',
    title: 'AdminMembersPageView',
    get about() {
      return t('catalog.adminMembersPageView.about')
    },
    file: 'src/views/admin/AdminMembersPageView.vue',
    component: AdminMembersPageView,
    needs: UI_T_R,
    teleport: true,
    args: {
      loading: false,
      roster: MEMBERS_ROSTER,
      error: null,
      candidates: [],
      searching: false,
      adding: false,
      removing: null,
      dialogOpen: false,
      search: '',
      selected: [],
      confirmHandle: null,
      notice: null,
    },
    states: [
      {
        get name() {
          return t('catalog.adminMembersPageView.s1.name')
        },
        get note() {
          return t('catalog.adminMembersPageView.s1.note')
        },
        props: {},
        expect: '林可',
      },
      {
        get name() {
          return t('catalog.adminMembersPageView.s2.name')
        },
        get note() {
          return t('catalog.adminMembersPageView.s2.note')
        },
        props: { roster: null, loading: true },
        expectSelector: '.agrid__bone',
      },
      {
        get name() {
          return t('catalog.adminMembersPageView.s3.name')
        },
        get note() {
          return t('catalog.adminMembersPageView.s3.note')
        },
        props: { roster: null, loading: false, error: '平台管理员名单接口超时' },
        expect: '平台管理员名单接口超时',
      },
      {
        get name() {
          return t('catalog.adminMembersPageView.s4.name')
        },
        get note() {
          return t('catalog.adminMembersPageView.s4.note')
        },
        props: { notice: '已加入 2 人' },
        expect: '已加入 2 人',
      },
      {
        get name() {
          return t('catalog.adminMembersPageView.s5.name')
        },
        get note() {
          return t('catalog.adminMembersPageView.s5.note')
        },
        props: { loading: true },
        expect: '林可',
        expectSelector: '.agrid--busy',
      },
      {
        get name() {
          return t('catalog.adminMembersPageView.s6.name')
        },
        get note() {
          return t('catalog.adminMembersPageView.s6.note')
        },
        props: { dialogOpen: true, candidates: MEMBERS_CANDIDATES },
        expect: '添加管理员',
      },
    ],
  },
]
