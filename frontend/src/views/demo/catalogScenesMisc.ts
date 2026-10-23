/**
 * 预览站里「小队与后台那几屏拆出来的半张画面」：我的团队、发现小队、团队算力、小队详情、
 * 小队那一页的页头与建队弹窗、成员页与「团队地址与加入」那张卡、飞书应用、空间申请。
 *
 * 这几屏在应用里由各自的路由页取数（`Mine.vue`、`Explore.vue`、`detail/Compute.vue`、
 * `Detail.vue`、`Index.vue`、`detail/Members.vue`、`AdminIntegrationsPage.vue`、
 * `AdminSpacesPage.vue`），路由页碰接口层，预览站碰不得。好在它们都已经拆成「路由页 → 只
 * 吃 props 的画面」：这里展示的正是产品里真会走到的那几格 —— 数据到货 / 首次加载 / 读失败 /
 * 空 / 没权限 / 表单与驳回框。各屏公用的一份数据见 `catalogScenesMiscFixtures.ts`。
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
  FEISHU_CONFIGURED,
  FEISHU_UNCONFIGURED,
  FOUND_USER,
  MY_TEAMS,
  SEARCH_TEAMS,
  team,
  TEAM_INVITATIONS,
  TEAM_JOIN_REQUESTS,
  TEAM_MEMBERS,
} from './catalogScenesMiscFixtures'

import { t } from '@/i18n'
import AdminIntegrationsPageView from '@/views/admin/AdminIntegrationsPageView.vue'
import AdminSpacesPageView from '@/views/admin/AdminSpacesPageView.vue'
import ComputeView from '@/views/teams/detail/ComputeView.vue'
import MembersView from '@/views/teams/detail/MembersView.vue'
import TeamJoinLinkCard from '@/views/teams/detail/TeamJoinLinkCard.vue'
import DetailView from '@/views/teams/DetailView.vue'
import ExploreView from '@/views/teams/ExploreView.vue'
import IndexView from '@/views/teams/IndexView.vue'
import MineView from '@/views/teams/MineView.vue'

/** 画法要 Vuetify、文案要语言包；那几条按 name 指路的去处（团队详情、我的设备）要一条 route。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 成员那一屏和团队链接那张卡都只吃 props：名单里的人名是一条静态的字，不指路。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

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
    about: '团队设备的管理和状态。示例数据。',
    file: 'src/views/teams/detail/ComputeView.vue',
    component: ComputeView,
    needs: UI_T_R,
    args: {
      devices: DEVICES,
      canManage: true,
      loading: false,
      loadFailed: false,
      reason: null,
      forbidden: false,
      busy: null,
      desktop: true,
      command:
        'curl -fsSL https://example.test/connector/install.sh | sh\ncheesehost --profile team-1 link connect https://example.test/connector',
      windowsCommand:
        'irm https://example.test/connector/install.ps1 | iex\ncheesehost --profile team-1 link connect https://example.test/connector',
      error: null,
    },
    states: [
      { name: '管理员', note: '团队管理员改名、移除每次接入。', props: {}, expect: '重命名' },
      { name: '成员', note: '成员只读。', props: { canManage: false }, expect: '书房的 Mac mini' },
      { name: '读取中', note: '正在读取。', props: { loading: true }, expectSelector: '.v-progress-circular' },
      {
        name: '读取失败',
        note: '提供重试。',
        props: { loadFailed: true, reason: '服务暂时不可用' },
        expect: '服务暂时不可用',
      },
      { name: '暂无设备', note: '没有接入。', props: { devices: [] }, expect: '暂无设备' },
    ],
  },
  {
    id: 'team-detail-view',
    title: 'DetailView',
    get about() {
      return t('catalog.teamDetailView.about')
    },
    file: 'src/views/teams/DetailView.vue',
    component: DetailView,
    needs: UI_T_R,
    args: {
      teamData: team(),
      isMember: true,
      notFound: false,
      failed: false,
      failureReason: null,
      forbidden: false,
      pageTitle: '城市数据实验室',
      pageWidth: 'read',
      teamIntro: '一起把城市数据用起来',
      // 「加入」走哪条接口由容器接上，预览站上走一遍就完：这里只看画出来的样子。
      join: async () => {},
    },
    states: [
      {
        get name() {
          return t('catalog.teamDetailView.s1.name')
        },
        get note() {
          return t('catalog.teamDetailView.s1.note')
        },
        props: {},
        // 小队名两面都画（成员这面在页头、非成员那面在资料卡里），简介那一句也是；认页头上那个 span，
        // 只有成员这一面才有（非成员那面走 TeamProfile）。
        expectSelector: '.team-intro',
      },
      {
        get name() {
          return t('catalog.teamDetailView.s2.name')
        },
        get note() {
          return t('catalog.teamDetailView.s2.note')
        },
        props: { isMember: false },
        expect: '加入团队',
      },
      {
        get name() {
          return t('catalog.teamDetailView.s3.name')
        },
        get note() {
          return t('catalog.teamDetailView.s3.note')
        },
        props: { teamData: undefined, notFound: true },
        expect: '找不到这个团队，它可能已解散或不对你公开',
      },
      {
        get name() {
          return t('catalog.teamDetailView.s4.name')
        },
        get note() {
          return t('catalog.teamDetailView.s4.note')
        },
        props: { teamData: undefined, failed: true, failureReason: 'connect ECONNREFUSED 127.0.0.1:5432' },
        expect: '团队信息加载失败',
      },
      {
        get name() {
          return t('catalog.teamDetailView.s5.name')
        },
        get note() {
          return t('catalog.teamDetailView.s5.note')
        },
        props: { teamData: undefined, failed: true, failureReason: null, forbidden: true },
        expect: '你没有权限查看',
      },
    ],
  },
  {
    id: 'team-index-view',
    title: 'IndexView',
    get about() {
      return t('catalog.teamIndexView.about')
    },
    file: 'src/views/teams/IndexView.vue',
    component: IndexView,
    needs: UI_T_R,
    teleport: true,
    args: {
      createTeamDialog: false,
      teamName: '',
      teamHandle: '',
      teamDescription: { type: 'doc', content: [] },
      teamAvatar: undefined,
      creatingTeam: false,
      teamNameError: '',
      teamHandleError: '',
      addressPrefix: 'cheese.example/teams/',
    },
    states: [
      {
        get name() {
          return t('catalog.teamIndexView.s1.name')
        },
        get note() {
          return t('catalog.teamIndexView.s1.note')
        },
        props: {},
        expect: '创建团队',
      },
      {
        get name() {
          return t('catalog.teamIndexView.s2.name')
        },
        get note() {
          return t('catalog.teamIndexView.s2.note')
        },
        props: { createTeamDialog: true },
        // 名字那格的占位是 placeholder 属性，不在 textContent 里；认地址那一格的标签。
        expect: '团队地址',
      },
      {
        get name() {
          return t('catalog.teamIndexView.s3.name')
        },
        get note() {
          return t('catalog.teamIndexView.s3.note')
        },
        props: { createTeamDialog: true, creatingTeam: true, teamName: '城市数据实验室', teamHandle: 'city-lab' },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() {
          return t('catalog.teamIndexView.s4.name')
        },
        get note() {
          return t('catalog.teamIndexView.s4.note')
        },
        props: {
          createTeamDialog: true,
          teamName: '城市数据实验室',
          teamNameError: '这个名称已被占用，换一个试试',
        },
        expect: '这个名称已被占用，换一个试试',
      },
      {
        get name() {
          return t('catalog.teamIndexView.s5.name')
        },
        get note() {
          return t('catalog.teamIndexView.s5.note')
        },
        props: {
          createTeamDialog: true,
          teamName: '城市数据实验室',
          teamHandle: 'city-lab',
          teamHandleError: '这个地址已被占用',
        },
        expect: '这个地址已被占用',
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
  {
    id: 'team-members-view',
    title: 'MembersView',
    get about() {
      return t('catalog.teamMembersView.about')
    },
    file: 'src/views/teams/detail/MembersView.vue',
    component: MembersView,
    needs: UI_T,
    teleport: true,
    args: {
      isSelfOwner: true,
      canBringPeopleIn: true,
      members: TEAM_MEMBERS,
      failedMembers: false,
      membersError: null,
      joinRequests: TEAM_JOIN_REQUESTS,
      loadingRequests: false,
      failedRequests: false,
      requestsError: null,
      invitations: TEAM_INVITATIONS,
      loadingInvitations: false,
      failedInvitations: false,
      invitationsError: null,
      found: null,
      lookingUp: false,
      lookupError: null,
      alreadyIn: false,
      activeTab: 'members',
      isInviteDialogActive: false,
      inviteQuery: '',
      inviteRoleInput: 'MEMBER',
      inviteMessageInput: '',
    },
    states: [
      {
        get name() {
          return t('catalog.teamMembersView.s1.name')
        },
        get note() {
          return t('catalog.teamMembersView.s1.note')
        },
        props: {},
        expect: '参与团队的项目',
      },
      {
        get name() {
          return t('catalog.teamMembersView.s2.name')
        },
        get note() {
          return t('catalog.teamMembersView.s2.note')
        },
        props: { activeTab: 'requests' },
        expect: '想跟着做城市数据的题',
      },
      {
        get name() {
          return t('catalog.teamMembersView.s3.name')
        },
        get note() {
          return t('catalog.teamMembersView.s3.note')
        },
        props: { activeTab: 'invitations' },
        expect: '艾琳',
      },
      {
        get name() {
          return t('catalog.teamMembersView.s4.name')
        },
        get note() {
          return t('catalog.teamMembersView.s4.note')
        },
        props: { members: [], failedMembers: true, membersError: '连接被拒绝' },
        expect: '无法加载成员',
      },
      {
        get name() {
          return t('catalog.teamMembersView.s5.name')
        },
        get note() {
          return t('catalog.teamMembersView.s5.note')
        },
        props: { activeTab: 'requests', joinRequests: [], loadingRequests: true },
        expectSelector: '.v-progress-circular',
      },
      {
        get name() {
          return t('catalog.teamMembersView.s6.name')
        },
        get note() {
          return t('catalog.teamMembersView.s6.note')
        },
        props: { isInviteDialogActive: true, inviteQuery: 'erin', found: FOUND_USER },
        expect: 'erin',
      },
      {
        get name() {
          return t('catalog.teamMembersView.s7.name')
        },
        get note() {
          return t('catalog.teamMembersView.s7.note')
        },
        props: { isInviteDialogActive: true, inviteQuery: 'erin', found: FOUND_USER, alreadyIn: true },
        expect: '已经在团队里',
      },
    ],
  },
  {
    id: 'team-join-link-card',
    title: 'TeamJoinLinkCard',
    get about() {
      return t('catalog.teamJoinLinkCard.about')
    },
    file: 'src/views/teams/detail/TeamJoinLinkCard.vue',
    component: TeamJoinLinkCard,
    needs: UI_T,
    args: {
      team: team({ visibility: 'public' }),
      link: { token: 'kJ3f9a21Xw', approval: true },
      busy: false,
      error: '',
      copied: false,
    },
    states: [
      {
        get name() {
          return t('catalog.teamJoinLinkCard.s1.name')
        },
        get note() {
          return t('catalog.teamJoinLinkCard.s1.note')
        },
        props: {},
        expect: '复制链接',
      },
      {
        get name() {
          return t('catalog.teamJoinLinkCard.s2.name')
        },
        get note() {
          return t('catalog.teamJoinLinkCard.s2.note')
        },
        props: { copied: true },
        expect: '已复制',
      },
      {
        get name() {
          return t('catalog.teamJoinLinkCard.s3.name')
        },
        get note() {
          return t('catalog.teamJoinLinkCard.s3.note')
        },
        props: { link: null, busy: true },
        expectSelector: '.v-progress-linear',
      },
      {
        get name() {
          return t('catalog.teamJoinLinkCard.s4.name')
        },
        get note() {
          return t('catalog.teamJoinLinkCard.s4.note')
        },
        props: { link: null, error: '操作失败，请重试' },
        expect: '操作失败，请重试',
      },
    ],
  },
]
