/**
 * 预览站里「小队与后台那几屏拆出来的半张画面」：我的团队、发现小队、团队算力、飞书应用、
 * 外站题账号、空间申请。
 *
 * 这几屏在应用里由各自的路由页取数（`Mine.vue`、`Explore.vue`、`detail/Compute.vue`、
 * `AdminIntegrationsPage.vue`、`AdminExternalProblemPage.vue`、`AdminSpacesPage.vue`），
 * 路由页碰接口层，预览站碰不得。
 * 好在它们都已经拆成「路由页 → 只吃 props 的画面」：这里展示的正是产品里真会走到的那
 * 几格 —— 数据到货 / 首次加载 / 读失败 / 空 / 没权限 / 表单与驳回框。各屏公用的一份
 * 数据见 `catalogScenesMiscFixtures.ts`。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的
 * `CatalogEntry`、`CatalogNeed` 是 type-only 引用：`catalog.ts` 反过来要分册的值，
 * 运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  APPLICATIONS,
  DEVICES,
  EXTERNAL_PROBLEM_CONFIGURED,
  EXTERNAL_PROBLEM_UNCONFIGURED,
  FEISHU_CONFIGURED,
  FEISHU_UNCONFIGURED,
  MY_DEVICES,
  MY_TEAMS,
  SEARCH_TEAMS,
  TEAM_DEVICE_ID,
} from './catalogScenesMiscFixtures'

import { t } from '@/i18n'
import AdminExternalProblemPageView from '@/views/admin/AdminExternalProblemPageView.vue'
import AdminIntegrationsPageView from '@/views/admin/AdminIntegrationsPageView.vue'
import AdminSpacesPageView from '@/views/admin/AdminSpacesPageView.vue'
import ComputeView from '@/views/teams/detail/ComputeView.vue'
import ExploreView from '@/views/teams/ExploreView.vue'
import MineView from '@/views/teams/MineView.vue'

/** 画法要 Vuetify、文案要语言包；那几条按 name 指路的去处（团队详情、我的设备）要一条 route。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const SCENES_MISC_ENTRIES: CatalogEntry[] = [
  {
    id: 'mine-view',
    title: 'MineView',
    get about() {
      return t('catalog.mineView.about')
    },
    file: 'src/views/teams/MineView.vue',
    component: MineView,
    needs: UI_T_R,
    args: { isLoadingMyTeams: false, loadMyTeamsError: false, myTeams: MY_TEAMS },
    states: [
      {
        get name() {
          return t('catalog.mineView.s1.name')
        },
        get note() {
          return t('catalog.mineView.s1.note')
        },
        props: {},
        expect: '城市数据实验室',
      },
      {
        get name() {
          return t('catalog.mineView.s2.name')
        },
        get note() {
          return t('catalog.mineView.s2.note')
        },
        props: { isLoadingMyTeams: true, myTeams: [] },
        expect: '加载中...',
      },
      {
        get name() {
          return t('catalog.mineView.s3.name')
        },
        get note() {
          return t('catalog.mineView.s3.note')
        },
        props: { loadMyTeamsError: true, myTeams: [] },
        expect: '加载失败',
      },
      {
        get name() {
          return t('catalog.mineView.s4.name')
        },
        get note() {
          return t('catalog.mineView.s4.note')
        },
        props: { myTeams: [] },
        expect: '还没有加入团队',
      },
    ],
  },
  {
    id: 'explore-view',
    title: 'ExploreView',
    get about() {
      return t('catalog.exploreView.about')
    },
    file: 'src/views/teams/ExploreView.vue',
    component: ExploreView,
    needs: UI_T_R,
    args: {
      mdAndUp: true,
      searchFailed: false,
      reason: null,
      forbidden: false,
      searchQuery: '',
      searchTeamsData: [],
      hasSearched: false,
      searchError: null,
    },
    states: [
      {
        get name() {
          return t('catalog.exploreView.s1.name')
        },
        get note() {
          return t('catalog.exploreView.s1.note')
        },
        props: {},
        expect: '招募广场',
      },
      {
        get name() {
          return t('catalog.exploreView.s2.name')
        },
        get note() {
          return t('catalog.exploreView.s2.note')
        },
        props: { searchQuery: '开源', hasSearched: true, searchTeamsData: SEARCH_TEAMS },
        expect: '开源小伙伴',
      },
      {
        get name() {
          return t('catalog.exploreView.s3.name')
        },
        get note() {
          return t('catalog.exploreView.s3.note')
        },
        props: { searchQuery: 'zzz', hasSearched: true, searchTeamsData: [] },
        expect: '没有找到团队',
      },
      {
        get name() {
          return t('catalog.exploreView.s4.name')
        },
        get note() {
          return t('catalog.exploreView.s4.note')
        },
        props: { hasSearched: true, searchFailed: true, reason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: 'connect ECONNREFUSED 127.0.0.1:5432',
      },
      {
        get name() {
          return t('catalog.exploreView.s5.name')
        },
        get note() {
          return t('catalog.exploreView.s5.note')
        },
        props: { hasSearched: true, searchFailed: true, reason: null, forbidden: true },
        expect: '你没有权限查看',
      },
    ],
  },
  {
    id: 'compute-view',
    title: 'ComputeView',
    get about() {
      return t('catalog.computeView.about')
    },
    file: 'src/views/teams/detail/ComputeView.vue',
    component: ComputeView,
    needs: UI_T_R,
    args: {
      scope: 'team',
      loading: false,
      loadFailed: false,
      reason: null,
      forbidden: false,
      addable: MY_DEVICES,
      busy: null,
      myDevices: MY_DEVICES,
      devices: DEVICES,
      onlineCount: 1,
      teamId: 1,
      myDeviceIds: new Set([TEAM_DEVICE_ID]),
      error: null,
    },
    states: [
      {
        get name() {
          return t('catalog.computeView.s1.name')
        },
        get note() {
          return t('catalog.computeView.s1.note')
        },
        props: {},
        expect: '书房的 Mac mini',
      },
      {
        get name() {
          return t('catalog.computeView.s2.name')
        },
        get note() {
          return t('catalog.computeView.s2.note')
        },
        props: { loading: true, devices: [] },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() {
          return t('catalog.computeView.s3.name')
        },
        get note() {
          return t('catalog.computeView.s3.note')
        },
        props: { loadFailed: true, reason: '服务暂时不可用', devices: [] },
        expect: '服务暂时不可用',
      },
      {
        get name() {
          return t('catalog.computeView.s4.name')
        },
        get note() {
          return t('catalog.computeView.s4.note')
        },
        props: { loadFailed: true, reason: null, forbidden: true, devices: [] },
        expect: '你没有权限查看',
      },
      {
        get name() {
          return t('catalog.computeView.s5.name')
        },
        get note() {
          return t('catalog.computeView.s5.note')
        },
        props: { devices: [], addable: [], myDevices: [], onlineCount: 0 },
        expect: '还没有自有设备',
      },
      {
        get name() {
          return t('catalog.computeView.s6.name')
        },
        get note() {
          return t('catalog.computeView.s6.note')
        },
        props: { error: '这台设备已经被别的团队占用了' },
        expect: '这台设备已经被别的团队占用了',
      },
    ],
  },
  {
    id: 'admin-integrations-page-view',
    title: 'AdminIntegrationsPageView',
    get about() {
      return t('catalog.adminIntegrationsPageView.about')
    },
    file: 'src/views/admin/AdminIntegrationsPageView.vue',
    component: AdminIntegrationsPageView,
    needs: UI_T_R,
    args: FEISHU_CONFIGURED,
    states: [
      {
        get name() {
          return t('catalog.adminIntegrationsPageView.s1.name')
        },
        get note() {
          return t('catalog.adminIntegrationsPageView.s1.note')
        },
        props: {},
        expect: '已配置 · cli_platform · feishu',
      },
      {
        get name() {
          return t('catalog.adminIntegrationsPageView.s2.name')
        },
        get note() {
          return t('catalog.adminIntegrationsPageView.s2.note')
        },
        props: { ...FEISHU_UNCONFIGURED },
        expect: '还没配置',
      },
      {
        get name() {
          return t('catalog.adminIntegrationsPageView.s3.name')
        },
        get note() {
          return t('catalog.adminIntegrationsPageView.s3.note')
        },
        props: { loadError: '飞书应用接口超时', showStatus: false },
        expect: '飞书应用接口超时',
      },
      {
        get name() {
          return t('catalog.adminIntegrationsPageView.s4.name')
        },
        get note() {
          return t('catalog.adminIntegrationsPageView.s4.note')
        },
        props: { appId: '', validationError: '先填 App ID' },
        expect: '先填 App ID',
      },
      {
        get name() {
          return t('catalog.adminIntegrationsPageView.s5.name')
        },
        get note() {
          return t('catalog.adminIntegrationsPageView.s5.note')
        },
        props: { saveError: 'App Secret 不能为空' },
        expect: 'App Secret 不能为空',
      },
    ],
  },
  {
    id: 'admin-external-problem-page-view',
    title: 'AdminExternalProblemPageView',
    get about() {
      return t('catalog.adminExternalProblemPageView.about')
    },
    file: 'src/views/admin/AdminExternalProblemPageView.vue',
    component: AdminExternalProblemPageView,
    needs: UI_T_R,
    args: EXTERNAL_PROBLEM_CONFIGURED,
    states: [
      {
        get name() {
          return t('catalog.adminExternalProblemPageView.s1.name')
        },
        get note() {
          return t('catalog.adminExternalProblemPageView.s1.note')
        },
        props: {},
        expect: '已配置 · teacher01 · yoj',
      },
      {
        get name() {
          return t('catalog.adminExternalProblemPageView.s2.name')
        },
        get note() {
          return t('catalog.adminExternalProblemPageView.s2.note')
        },
        props: { ...EXTERNAL_PROBLEM_UNCONFIGURED },
        expect: '还没配置',
      },
      {
        get name() {
          return t('catalog.adminExternalProblemPageView.s3.name')
        },
        get note() {
          return t('catalog.adminExternalProblemPageView.s3.note')
        },
        props: { loadError: '外站题账号接口超时', showStatus: false },
        expect: '外站题账号接口超时',
      },
      {
        get name() {
          return t('catalog.adminExternalProblemPageView.s4.name')
        },
        get note() {
          return t('catalog.adminExternalProblemPageView.s4.note')
        },
        props: { username: '', validationError: '先填账号' },
        expect: '先填账号',
      },
      {
        get name() {
          return t('catalog.adminExternalProblemPageView.s5.name')
        },
        get note() {
          return t('catalog.adminExternalProblemPageView.s5.note')
        },
        props: { saveError: '账号或密码不对' },
        expect: '账号或密码不对',
      },
    ],
  },
  {
    id: 'admin-spaces-page-view',
    title: 'AdminSpacesPageView',
    get about() {
      return t('catalog.adminSpacesPageView.about')
    },
    file: 'src/views/admin/AdminSpacesPageView.vue',
    component: AdminSpacesPageView,
    needs: UI_T_R,
    layout: false,
    teleport: true,
    args: {
      status: 'PENDING',
      items: APPLICATIONS,
      loading: false,
      loadError: null,
      offset: 0,
      hasMore: false,
      saving: false,
      pageSize: 50,
      writeError: '',
      selected: null,
      reason: '',
    },
    states: [
      {
        get name() {
          return t('catalog.adminSpacesPageView.s1.name')
        },
        get note() {
          return t('catalog.adminSpacesPageView.s1.note')
        },
        props: {},
        expect: '计算机视觉兴趣组',
      },
      {
        get name() {
          return t('catalog.adminSpacesPageView.s2.name')
        },
        get note() {
          return t('catalog.adminSpacesPageView.s2.note')
        },
        props: { loading: true, items: [] },
        expectSelector: '.asp__bone',
      },
      {
        get name() {
          return t('catalog.adminSpacesPageView.s3.name')
        },
        get note() {
          return t('catalog.adminSpacesPageView.s3.note')
        },
        props: { loadError: '连接被拒绝', items: [] },
        expect: '连接被拒绝',
      },
      {
        get name() {
          return t('catalog.adminSpacesPageView.s4.name')
        },
        get note() {
          return t('catalog.adminSpacesPageView.s4.note')
        },
        props: { items: [] },
        expect: '暂无申请。',
      },
      {
        get name() {
          return t('catalog.adminSpacesPageView.s5.name')
        },
        get note() {
          return t('catalog.adminSpacesPageView.s5.note')
        },
        props: { selected: APPLICATIONS[0] },
        expect: '驳回原因',
      },
      {
        get name() {
          return t('catalog.adminSpacesPageView.s6.name')
        },
        get note() {
          return t('catalog.adminSpacesPageView.s6.note')
        },
        props: { offset: 50, hasMore: true },
        expect: '上一页',
      },
    ],
  },
]
