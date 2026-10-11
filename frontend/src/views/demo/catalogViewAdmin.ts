/**
 * 预览站里「后台那一组整页」的前半：方案与额度、棘轮、运行记录、协议文档。
 *
 * 这些页面在应用里由各自的路由页取数（`AdminCreditsPage.vue`、`AdminRatchetPage.vue`、
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
  CRV_HISTORY,
  CRV_LEGAL,
  CRV_PLANS,
  CRV_RATCHET,
  CRV_RUN_DETAIL,
  CRV_RUN_OVERVIEW,
  CRV_TEAM_DETAIL,
  CRV_TEAMS,
  CRV_TIER_MODELS,
} from './catalogViewAdminFixtures'

import AdminCreditsPageView from '@/views/admin/AdminCreditsPageView.vue'
import AdminRatchetPageView from '@/views/admin/AdminRatchetPageView.vue'
import AdminRunRecordsPageView from '@/views/admin/AdminRunRecordsPageView.vue'
import LegalDocumentView from '@/views/legal/LegalDocumentView.vue'

/** 画法要 Vuetify、文案要语言包；KPI 卡、对话链接那一类还要一条 route。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_ADMIN_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-credits-page-view',
    title: 'AdminCreditsPageView',
    about:
      '方案与额度这一页：上面方案一览、下面团队一览（带方案 / 类型筛选和搜索），点开一个团队、新建一个方案、发一笔额度都是弹出来的浮层。',
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
      grantOpen: false,
      grantSaving: false,
      grantError: null,
    },
    states: [
      {
        name: '方案与团队都到货了',
        note: '默认这一格：方案表在上、团队表在下，两张表都画出了真数据 —— 团队那一栏里个人团队写主人的昵称，共享团队写成员数。',
        props: {},
        expect: '城市数据实验室',
      },
      {
        name: '首次加载',
        note: '两份都还没到货：两张表各画自己的骨架，形状和到货后一样，不重排。',
        props: { plans: null, plansLoading: true, teams: null, teamsLoading: true },
      },
      {
        name: '团队表读不到',
        note: '团队那一路挂了不牵连方案那一路：只有团队那一块换成「读不到 + 原话 + 重试」，方案表照画。',
        props: { teams: null, teamsError: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '无法读取团队',
      },
      {
        name: '点开了一个团队',
        note: '点某一行团队时右边（手机上整屏）滑出面板：方案、每一笔额度、操作记录；发额度用的是一颗按钮。',
        props: { panelOpen: true, team: CRV_TEAM_DETAIL, history: CRV_HISTORY },
        expect: '管理员发放',
      },
      {
        name: '新建方案',
        note: '「新建方案」那颗按钮弹出来的框：名称、适用对象、额度与上限、可用模型都空着等人填。',
        props: { planDialogOpen: true, planEditing: null },
        expect: '新建方案',
      },
    ],
  },
  {
    id: 'admin-ratchet-page-view',
    title: 'AdminRatchetPageView',
    about:
      '棘轮这一屏：每道架构检查现在是多少、冻结了多少、比起点好了还是差了。数据从哪来写在来源行上，拉取的结果浮在顶上。',
    file: 'src/views/admin/AdminRatchetPageView.vue',
    component: AdminRatchetPageView,
    needs: UI_T_R,
    args: { board: CRV_RATCHET },
    states: [
      {
        name: '归档里有两次采集',
        note: '默认这一格：来源行说清数据来自哪个提交、什么时候采的、归档里一共几次，下面按区摆每一道检查现在的数和它的走向。',
        props: {},
        expect: '现在',
      },
      {
        name: '首次加载',
        note: '还没拿到 board：三条高度固定的骨架，不画空表也不画空态。',
        props: { board: null, loading: true },
        expectSelector: '.arc__skel',
      },
      {
        name: '归档读不出来',
        note: '这次请求挂了、手上没有 board：说清是「归档读不出来」而不是「还没有采集」——两件事两句话，重试就在旁边。',
        props: { board: null, failed: true },
        expect: '归档读不出来',
      },
      {
        name: '一次都还没采过',
        note: '归档里本来就空：是「还没有一次采集」，给一颗「去拉一次」，不画成「一切正常」。',
        props: { board: { ...CRV_RATCHET, points: 0, total_stored: 0, collections: [], areas: [] } },
        expect: '还没有一次采集',
      },
      {
        name: '采过，但一条测量都没有',
        note: '归档里有采集、却没有一道检查（真实契约里一次失败的采集就是这样）：说清这次没有可用的测量，不藏起失败历史。',
        props: {
          board: {
            ...CRV_RATCHET,
            points: 1,
            total_stored: 1,
            collection: 'failed',
            collections: [
              {
                commit: 'aaed1c00',
                collected_at: '2026-09-29T09:00:00+00:00',
                run_url: 'https://example.test/run/aaed',
                collection: 'failed',
                reason: 'no interpreter (exit 127)',
              },
            ],
            areas: [],
          },
        },
        expect: '这次没有可用的测量',
      },
      {
        name: '拉取失败的那一条',
        note: '刚按过刷新、CI 那边没拉到：顶上浮一条红色说明，页面留下的还是已经存下的那些点。',
        props: { pull: '拉取失败：no interpreter (exit 127)', pullFailed: true },
        expect: '拉取失败：no interpreter (exit 127)',
      },
    ],
  },
  {
    id: 'admin-run-records-page-view',
    title: 'AdminRunRecordsPageView',
    about:
      '运行记录这一屏：平台在各个项目里遇到的报错、和它自己处理掉的事，一种一行，点开一行右边是那种事最近一次的全文和它出现在哪些项目、对话里。',
    file: 'src/views/admin/AdminRunRecordsPageView.vue',
    component: AdminRunRecordsPageView,
    needs: UI_T_R,
    args: { overview: CRV_RUN_OVERVIEW },
    states: [
      {
        name: '一种事一行',
        note: '默认这一格：报错在前、平台自己处理掉的在后；页头三张 KPI 说清有几种报错、一共几次、平台自己处理了几次。',
        props: {},
        expect: 'GET /api/site 超时',
      },
      {
        name: '首次加载',
        note: '还没拿到总览：三张 KPI 画骨架，表还没起。',
        props: { overview: null, loading: true },
        expectSelector: '[role="table"]',
      },
      {
        name: '读失败不说「暂无」',
        note: '接口挂了要留在原地：说清读失败并给重试，而不是画成「暂无运行记录」——那会把失败藏成没有。',
        props: { overview: null, failed: true },
        expectSelector: '[role="alert"]',
      },
      {
        name: '暂时没有记录',
        note: '读过、窗口里确实一种都没有：这时才说「暂无运行记录」。',
        props: { overview: { ...CRV_RUN_OVERVIEW, groups: [], totals: { error_kinds: 0, errors: 0, recovered: 0 } } },
        expect: '暂无运行记录',
      },
      {
        name: '点开一种，看它的详情',
        note: '点某一行右边滑出详情：最近一次的原文、它出现在哪些项目对话里（点得进去）、调用栈。',
        props: { selected: 'backend_error:fp-site', detail: CRV_RUN_DETAIL },
        expect: 'GET /api/site 超时：上游 30 秒没有回应',
      },
    ],
  },
  {
    id: 'legal-document-view',
    title: 'LegalDocumentView',
    about:
      '一份已经发布的用户协议或隐私政策：版本、生效日期，和它自己的正文；正文由容器取好交给它，它自己一字节都不取。',
    file: 'src/views/legal/LegalDocumentView.vue',
    component: LegalDocumentView,
    needs: ['i18n'],
    args: { doc: CRV_LEGAL, error: '' },
    states: [
      {
        name: '一份协议',
        note: '默认这一格：顶上写清版本和生效日期，下面是把 Markdown 画出来的正文。',
        props: {},
        expect: '版本 2026-01-01',
      },
      {
        name: '读不出来',
        note: '这份版本没取到时用一句原因顶替正文，不画成空白的一页。',
        props: { doc: null, error: '暂时无法加载，刷新页面重试' },
        expect: '暂时无法加载，刷新页面重试',
      },
    ],
  },
]
