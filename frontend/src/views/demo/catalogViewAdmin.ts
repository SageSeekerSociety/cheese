/**
 * 预览站里「后台那一组整页」的前半：方案与额度、运行记录、协议文档。
 *
 * 这些页面在应用里由各自的路由页取数（`AdminCreditsPage.vue`、
 * `AdminRunRecordsPage.vue`、`LegalDocument.vue`），路由页碰接口层，预览站碰不得。好在
 * 它们都已经拆成「路由页 → 只吃 props 的画面」：这里展示的正是产品里真会走到的那几格 ——
 * 数据到货 / 首次加载 / 读失败 / 空 / 点开了某一行的详情。各屏公用的一份数据见
 * `catalogViewAdminFixtures.ts`。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和 `catalogAdmin.ts`、
 * `catalogViews.ts` 同一个理由。这里的 `CatalogEntry`、`CatalogNeed` 是 type-only 引用：
 * `catalog.ts` 反过来要分册的值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  CRV_CI_BOARD,
  CRV_CI_JOBS,
  CRV_CI_STEPS,
  CRV_HISTORY,
  CRV_LEGAL,
  CRV_MACHINES,
  CRV_PLANS,
  CRV_RUN_DETAIL,
  CRV_RUN_OVERVIEW,
  CRV_TEAM_DETAIL,
  CRV_TEAMS,
  CRV_TIER_MODELS,
} from './catalogViewAdminFixtures'

import { t } from '@/i18n'
import AdminCiActivityPageView from '@/views/admin/AdminCiActivityPageView.vue'
import AdminCreditsPageView from '@/views/admin/AdminCreditsPageView.vue'
import AdminMachinesPageView from '@/views/admin/AdminMachinesPageView.vue'
import AdminRunRecordsPageView from '@/views/admin/AdminRunRecordsPageView.vue'
import LegalDocumentView from '@/views/legal/LegalDocumentView.vue'

/** 画法要 Vuetify、文案要语言包；KPI 卡、对话链接那一类还要一条 route。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_ADMIN_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-credits-page-view',
    title: 'AdminCreditsPageView',
    get about() {
      return t('catalog.adminCreditsPageView.about')
    },
    file: 'src/views/admin/AdminCreditsPageView.vue',
    component: AdminCreditsPageView,
    needs: UI_T_R,
    layout: true,
    teleport: true,
    args: {
      plans: CRV_PLANS,
      plansLoading: false,
      plansError: null,
      tierModels: CRV_TIER_MODELS,
      planDialogOpen: false,
      planEditing: null,
      planSaving: false,
      planSaveError: null,
      query: '',
      planFilter: null,
      kindFilter: null,
      teams: CRV_TEAMS,
      teamsLoading: false,
      teamsError: null,
      searching: false,
      panelOpen: false,
      team: null,
      teamLoading: false,
      teamError: null,
      history: null,
      historyError: null,
      teamPlanSaving: false,
      teamPlanError: null,
      overseasSaving: false,
      overseasError: null,
      grantOpen: false,
      grantSaving: false,
      grantError: null,
    },
    states: [
      {
        get name() {
          return t('catalog.adminCreditsPageView.s1.name')
        },
        get note() {
          return t('catalog.adminCreditsPageView.s1.note')
        },
        props: {},
        expect: '城市数据实验室',
      },
      {
        get name() {
          return t('catalog.adminCreditsPageView.s2.name')
        },
        get note() {
          return t('catalog.adminCreditsPageView.s2.note')
        },
        props: { plans: null, plansLoading: true, teams: null, teamsLoading: true },
      },
      {
        get name() {
          return t('catalog.adminCreditsPageView.s3.name')
        },
        get note() {
          return t('catalog.adminCreditsPageView.s3.note')
        },
        props: { teams: null, teamsError: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '无法读取团队',
      },
      {
        get name() {
          return t('catalog.adminCreditsPageView.s4.name')
        },
        get note() {
          return t('catalog.adminCreditsPageView.s4.note')
        },
        props: { panelOpen: true, team: CRV_TEAM_DETAIL, history: CRV_HISTORY },
        expect: '管理员发放',
      },
      {
        get name() {
          return t('catalog.adminCreditsPageView.s5.name')
        },
        get note() {
          return t('catalog.adminCreditsPageView.s5.note')
        },
        props: { planDialogOpen: true, planEditing: null },
        expect: '新建方案',
      },
    ],
  },
  {
    id: 'admin-ci-activity-page-view',
    title: 'AdminCiActivityPageView',
    get about() {
      return t('catalog.adminCiActivityPageView.about')
    },
    file: 'src/views/admin/AdminCiActivityPageView.vue',
    component: AdminCiActivityPageView,
    needs: UI_T_R,
    args: { board: CRV_CI_BOARD, days: 7, jobs: CRV_CI_JOBS },
    states: [
      {
        get name() {
          return t('catalog.adminCiActivityPageView.s1.name')
        },
        get note() {
          return t('catalog.adminCiActivityPageView.s1.note')
        },
        props: {},
        expect: 'e2e / e2e',
      },
      {
        get name() {
          return t('catalog.adminCiActivityPageView.s2.name')
        },
        get note() {
          return t('catalog.adminCiActivityPageView.s2.note')
        },
        props: { job: CRV_CI_JOBS[0], steps: CRV_CI_STEPS, runner: 'cheese-ci-runner-1' },
        expect: 'Run e2e',
      },
      {
        get name() {
          return t('catalog.adminCiActivityPageView.s3.name')
        },
        get note() {
          return t('catalog.adminCiActivityPageView.s3.note')
        },
        props: {
          board: {
            ...CRV_CI_BOARD,
            sweep: {
              at: '2026-10-17T22:35:00+00:00',
              error: 'CiQuotaRefused: GitHub quota spent (HTTP 403)',
              jobs_owed: 4210,
              covered_from: '2026-10-12T04:00:00+00:00',
              complete: false,
            },
          },
        },
        expect: 'GitHub quota spent',
      },
      {
        get name() {
          return t('catalog.adminCiActivityPageView.s4.name')
        },
        get note() {
          return t('catalog.adminCiActivityPageView.s4.note')
        },
        props: { board: null, jobs: [], failed: true },
        expectSelector: '.v-btn, button',
      },
    ],
  },
  {
    id: 'admin-run-records-page-view',
    title: 'AdminRunRecordsPageView',
    get about() {
      return t('catalog.adminRunRecordsPageView.about')
    },
    file: 'src/views/admin/AdminRunRecordsPageView.vue',
    component: AdminRunRecordsPageView,
    needs: UI_T_R,
    args: { overview: CRV_RUN_OVERVIEW },
    states: [
      {
        get name() {
          return t('catalog.adminRunRecordsPageView.s1.name')
        },
        get note() {
          return t('catalog.adminRunRecordsPageView.s1.note')
        },
        props: {},
        expect: 'GET /api/site 超时',
      },
      {
        get name() {
          return t('catalog.adminRunRecordsPageView.s2.name')
        },
        get note() {
          return t('catalog.adminRunRecordsPageView.s2.note')
        },
        props: { overview: null, loading: true },
        // 总览还没到：三张 KPI 画骨架，表头已经摆出来、但还没有数据行。`:only-child` 钉住
        // 「表里只剩这截表头」：数据到货那一格，表头后面跟着一行行按钮，就不再是独苗。
        expectSelector: '.rr-table .rr-row--head:only-child',
      },
      {
        get name() {
          return t('catalog.adminRunRecordsPageView.s3.name')
        },
        get note() {
          return t('catalog.adminRunRecordsPageView.s3.note')
        },
        props: { overview: null, failed: true },
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.adminRunRecordsPageView.s4.name')
        },
        get note() {
          return t('catalog.adminRunRecordsPageView.s4.note')
        },
        props: { overview: { ...CRV_RUN_OVERVIEW, groups: [], totals: { error_kinds: 0, errors: 0, recovered: 0 } } },
        expect: '暂无运行记录',
      },
      {
        get name() {
          return t('catalog.adminRunRecordsPageView.s5.name')
        },
        get note() {
          return t('catalog.adminRunRecordsPageView.s5.note')
        },
        props: { selected: 'backend_error:fp-site', detail: CRV_RUN_DETAIL },
        expect: 'GET /api/site 超时：上游 30 秒没有回应',
      },
    ],
  },
  {
    id: 'admin-machines-page-view',
    title: 'AdminMachinesPageView',
    get about() {
      return t('catalog.adminMachinesPageView.about')
    },
    file: 'src/views/admin/AdminMachinesPageView.vue',
    component: AdminMachinesPageView,
    needs: UI_T_R,
    args: { overview: CRV_MACHINES },
    states: [
      {
        get name() {
          return t('catalog.adminMachinesPageView.s1.name')
        },
        get note() {
          return t('catalog.adminMachinesPageView.s1.note')
        },
        props: {},
        expect: 'cheese-pool-7f3a',
      },
      {
        get name() {
          return t('catalog.adminMachinesPageView.s2.name')
        },
        get note() {
          return t('catalog.adminMachinesPageView.s2.note')
        },
        props: {
          overview: { ...CRV_MACHINES, pool: { ...CRV_MACHINES.pool, provider_failures: 3, refusing: true } },
        },
        expect: '此刻不再接新的 AI 队友',
      },
      {
        get name() {
          return t('catalog.adminMachinesPageView.s3.name')
        },
        get note() {
          return t('catalog.adminMachinesPageView.s3.note')
        },
        props: { overview: null, failed: true },
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.adminMachinesPageView.s4.name')
        },
        get note() {
          return t('catalog.adminMachinesPageView.s4.note')
        },
        props: {
          overview: {
            ...CRV_MACHINES,
            pool: { ...CRV_MACHINES.pool, hosts: 0, slots: 0, used: 0, free: 0, waiting: 0, oldest_wait_since: null },
            hosts: [],
            warm: [],
          },
        },
        expect: '此刻一台宿主机都没有',
      },
    ],
  },
  {
    id: 'legal-document-view',
    title: 'LegalDocumentView',
    get about() {
      return t('catalog.legalDocumentView.about')
    },
    file: 'src/views/legal/LegalDocumentView.vue',
    component: LegalDocumentView,
    needs: ['i18n'],
    args: { doc: CRV_LEGAL, error: '' },
    states: [
      {
        get name() {
          return t('catalog.legalDocumentView.s1.name')
        },
        get note() {
          return t('catalog.legalDocumentView.s1.note')
        },
        props: {},
        expect: '版本 2026-01-01',
      },
      {
        get name() {
          return t('catalog.legalDocumentView.s2.name')
        },
        get note() {
          return t('catalog.legalDocumentView.s2.note')
        },
        props: { doc: null, error: '暂时无法加载，刷新页面重试' },
        expect: '暂时无法加载，刷新页面重试',
      },
    ],
  },
]
