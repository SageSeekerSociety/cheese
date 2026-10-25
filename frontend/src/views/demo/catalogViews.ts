/**
 * 预览站里「整页」那一组：一项产物交付过的每一版，和小队的对外一面。
 *
 * 这两件以前要一整套东西才看得出来（一条真产物、一份真小队、路由、外壳），现在各自
 * 只吃 props：版本表收一个 `versions` 数组和它所在的 `projectId`，「加入」的函数由调用
 * 方给；小队简介收一份 `Team`。所以它们能单独摆进来 —— 这里展示的正是产品里真会出现的
 * 那几种状态（刚交付/file/link/merge、没交付过、版本多到要收起来；没加入要审批、不用
 * 审批、已经是成员、申请交过了）。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的 `CatalogEntry`
 * 是 type-only 引用，`catalog.ts` 反过来要 `VIEW_ENTRIES` 这个值，运行时不构成循环。
 * 数据见 `catalogSharedFixtures.ts`。
 */
import type { SpaceApplication } from '@/types'
import type { CatalogEntry, CatalogNeed } from './catalog'

import { VIEW_ASSISTANT_ENTRIES } from './catalogAssistant'
import {
  ARTIFACT_VERSIONS,
  ARTIFACT_VERSIONS_MANY,
  ARTIFACT_VERSIONS_WITHOUT_FILE,
  teamProfile,
} from './catalogSharedFixtures'
import { VIEW_SPACE_TASKS_ENTRIES } from './catalogSpaceTasks'
import { VIEW_ACCOUNT_ENTRIES } from './catalogViewAccount'
import { VIEW_ADMIN_ENTRIES } from './catalogViewAdmin'
import { VIEW_ADMIN_STATS_ENTRIES } from './catalogViewAdminStats'
import { VIEW_SETTINGS_WORKSPACE_ENTRIES } from './catalogViewSettingsWorkspace'

import ArtifactVersionList from '@/views/artifact/ArtifactVersionList.vue'
import SpaceApplicationView from '@/views/spaces/SpaceApplicationView.vue'
import TeamProfile from '@/views/teams/TeamProfile.vue'
import WelcomeView from '@/views/welcome/WelcomeView.vue'

/** 两件都要 Vuetify 的画法、一套语言包，卡片按钮还要一条 name 指得上的路由。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 「加入」这一趟接口由调用方给，预览站上走一遍就完：只看画出来的样子。 */
const join = async () => {}

/** 我建的、还没过审的两份申请：一份在等审，一份被驳回带原因。 */
const APPLICATION_PENDING: SpaceApplication = {
  id: 8,
  avatarId: null,
  name: '操作系统 2026 秋',
  intro: '本科三年级必修课。五个实验从系统调用做到文件系统，基于 xv6。',
  reviewStatus: 'PENDING',
  description: '',
  owner: 'linxia',
  reviewReason: null,
  reviewedBy: null,
  reviewedAt: null,
  createdAt: '2026-10-18T08:00:00Z',
}
const APPLICATION_REJECTED: SpaceApplication = {
  ...APPLICATION_PENDING,
  id: 9,
  name: '量子计算导论',
  intro: '介绍量子比特、量子门和基本量子算法，配合 Qiskit 做模拟实验。',
  reviewStatus: 'REJECTED',
  reviewReason: '空间名称请写明开课学期，简介里写清这门课面向哪个年级。',
  reviewedBy: 'cheese',
  reviewedAt: '2026-10-19T03:12:00Z',
}

export const VIEW_ENTRIES: CatalogEntry[] = [
  // 账号那一组整页（登录进门到设置密码的每一屏）单放一册，展开进来。
  ...VIEW_ACCOUNT_ENTRIES,
  // 后台那一组整页（方案与额度、棘轮、运行记录、协议、统计）各单放一册，展开进来。
  ...VIEW_ADMIN_ENTRIES,
  ...VIEW_ADMIN_STATS_ENTRIES,
  // 空间与题目那一组整页（空间设置四栏、发题页、被拒进门，和题目详情下三个页签）也在
  // 自己那一册里，展开进来。
  ...VIEW_SPACE_TASKS_ENTRIES,
  ...VIEW_ASSISTANT_ENTRIES,
  // 账号设置的那几屏（通用、通知、资料、实名、密码与安全、这台设备）和项目工作台的那几
  // 屏（全部频道、全部任务、项目总览）也在自己那一册里，展开进来。
  ...VIEW_SETTINGS_WORKSPACE_ENTRIES,
  {
    id: 'artifact-version-list',
    title: 'ArtifactVersionList',
    about:
      '一项产物交付过的每一版，最新的在最上面：每一行说清这次改了什么、谁什么时候认的、在哪次对话里做出来的，能拿走的就在这一行上拿，要比比上一版也从这一行进。',
    file: 'src/views/artifact/ArtifactVersionList.vue',
    component: ArtifactVersionList,
    needs: UI_T_R,
    args: { projectId: 'p-demo', downloading: '' },
    states: [
      {
        name: '交付过的每一版',
        note: '一份文件、一个网址、一次合并在同一条时间线上：最新那一版带「当前」，认过它的那一行写着是谁采纳的，做着它的那次对话能点回去。',
        props: { versions: ARTIFACT_VERSIONS },
        expect: 'feat: 把月度报告导成 PDF',
      },
      {
        name: '交出去的不是文件',
        note: '两版都没有文件可给，但说的不是同一句话：一次合并没有文件，交付物留存之前递的那一版是没留存 —— 分得开才不会误以为东西丢了。',
        props: { versions: ARTIFACT_VERSIONS_WITHOUT_FILE },
        expect: '交出去的是这次合并',
      },
      {
        name: '一版都没有',
        note: '一版都没交付过时不留一块空白：就地说一句「暂无交付」。',
        props: { versions: [] },
        expect: '暂无交付',
      },
      {
        name: '做久了，先摆最近几版',
        note: '版本多了先摆最近四版，剩下的收进一颗按钮，几点它就是剩下几条（这里要做 6 版，先收 2 条）。',
        props: { versions: ARTIFACT_VERSIONS_MANY },
        expect: '显示更早的 2 版',
      },
    ],
  },
  {
    id: 'welcome-view',
    title: 'WelcomeView',
    about: '新账号的新手引导：两个问题一屏一问，右边的「你的项目」随回答长出来；点「建好项目」才把回答交给页面去建。',
    file: 'src/views/welcome/WelcomeView.vue',
    component: WelcomeView,
    needs: UI_T_R,
    args: {
      me: { username: 'lin', nickname: '林小满', work_role: null, organization: null },
      busy: false,
      failure: '',
    },
    states: [
      {
        name: '第一次进来',
        note: '从身份问起，称呼用账号上的昵称；不想答可以跳过引导。',
        props: {},
        expect: '你主要用知是做什么？',
      },
      {
        name: '从帮助菜单再走一遍',
        note: '上次选的身份已经按下，没有「跳过引导」。',
        props: {
          again: true,
          me: { username: 'lin', nickname: '林小满', work_role: 'teacher', organization: '中国人民大学' },
        },
        expect: '你主要用知是做什么？',
      },
      {
        name: '没建成',
        note: '建项目失败时就地说原因，人留在这一页，可以再点一次。',
        props: { failure: '没建成项目：代码仓库服务响应超时' },
        expect: '代码仓库服务响应超时',
      },
    ],
  },
  {
    id: 'team-profile',
    title: 'TeamProfile',
    about:
      '小队的对外一面：没加入的人按地址打开看到的就是这一页 —— 名字、介绍、成员数，和一颗「加入」；小队开着审批时，按下去交的是申请（带理由），等着管理员批。',
    file: 'src/views/teams/TeamProfile.vue',
    component: TeamProfile,
    needs: UI_T_R,
    args: { team: teamProfile(), join },
    states: [
      {
        name: '还没加入，小队要审批',
        note: '开着审批的小队：先说清加入要人批准，所以下面多一栏写申请理由，按下去交出去的是申请，不是直接成为成员。',
        props: { team: teamProfile({ joinStatus: 'none', joinApproval: true }) },
        expect: '申请加入',
      },
      {
        name: '加入不用审批',
        note: '小队开着门、不用审批：没有理由那一栏，按下去直接成为成员。',
        props: { team: teamProfile({ joinStatus: 'none', joinApproval: false }) },
        expect: '加入团队',
      },
      {
        name: '已经是成员',
        note: '已经在里面的人看到的是另一件事：一句「你已经在这个团队里」，和一颗进团队主页的按钮。',
        props: { team: teamProfile({ joinStatus: 'member' }) },
        expect: '进入团队',
      },
      {
        name: '申请交过了，等审批',
        note: '申请交出去之后停在原地等：这一格说的就是「已提交申请，等待团队管理员审批」，没有别的东西可按。',
        props: { team: teamProfile({ joinStatus: 'pending' }) },
        expect: '已提交申请，等待团队管理员审批',
      },
    ],
  },
  {
    id: 'space-application-view',
    title: 'SpaceApplicationView',
    about:
      '我建的空间还没过审时，首页侧栏那一行点进来的一页：待审核时可以撤回；被驳回时看驳回原因、改了重新提交，或者删掉这份申请。取数、提交、确认都在容器 `SpaceApplication.vue`。',
    file: 'src/views/spaces/SpaceApplicationView.vue',
    component: SpaceApplicationView,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '待审核',
        note: '刚提交、还没人审：写名称和简介，能做的只有撤回。',
        props: { application: APPLICATION_PENDING },
        expect: '待平台管理员审核',
      },
      {
        name: '被驳回，带原因',
        note: '审核人写了原因就照写；下面是那份申请原来的样子，改了可以重新提交，也可以删掉。',
        props: { application: APPLICATION_REJECTED },
        expect: '简介里写清这门课面向哪个年级',
      },
      {
        name: '重新提交没成',
        note: '那一句画在表单底下，改过的字还在。',
        props: { application: APPLICATION_REJECTED, error: '重新提交失败，稍后重试' },
        expect: '重新提交失败，稍后重试',
      },
      {
        name: '读不到这份申请',
        note: '编号不对，或者已经撤回：说一句读不到，给一颗重试。',
        props: { application: null, loadError: '无法读取这份申请' },
        expect: '无法读取这份申请',
      },
    ],
  },
]
