/**
 * 首页那一格在预览站里的条目：那份目录（`HomeNav.vue`）、它两端的两个画面
 * （`HomeSidebarView.vue`、`HomeHubView.vue`），和它带的四个弹窗。
 *
 * 单独一份是因为 `catalog.ts` 已经九百多行，再塞七条会顶到 `frontend/src` 那一千行
 * 的上限 —— 和 `catalogRoom.ts`、`catalogChannelFiles.ts` 同一批文件同一个理由。
 *
 * 七件都是「只吃 props、只往上发事件」的那一档：名单、待办数、四个弹窗的开合与它们的
 * 进行中 / 出错，全从 props 进；取数、写回后端、跳地址在 `composables/useTeamNav.ts`，
 * 所以这里不用起假后端。目录自己记着「哪几行摊开」这件事（在这台浏览器的 localStorage
 * 里），预览站上点到哪一行就摊开哪一行，那一格的样子自己在页面上试。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `HOME_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { Team, TeamMember } from '@/types'
import type { CatalogEntry, CatalogNeed } from './catalog'

import DisbandTeamDialog from '@/views/teams/DisbandTeamDialog.vue'
import TeamProfileEditDialog from '@/views/teams/TeamProfileEditDialog.vue'
import TransferTeamDialog from '@/views/teams/TransferTeamDialog.vue'

import HomeHubView from '@/views/home/HomeHubView.vue'
import HomeNav from '@/views/home/HomeNav.vue'
import HomeSidebarView from '@/views/home/HomeSidebarView.vue'
import JoinSpaceDialog from '@/views/home/JoinSpaceDialog.vue'

/** 目录里的字要 i18n，行、按钮、抽屉要 vuetify，带 `to` 的行还要路由（`demoRouter`）。 */
const NAV_T: CatalogNeed[] = ['vuetify', 'i18n', 'router']
/** 侧栏那一端还多一样：抽屉自己的开合记在 pinia 里。 */
const SIDEBAR_T: CatalogNeed[] = [...NAV_T, 'pinia']

function team(handle: string, name: string, role: Team['role'], extra: Partial<Team> = {}): Team {
  return {
    id: [...handle].reduce((n, c) => n + c.charCodeAt(0), 0),
    handle,
    name,
    intro: '',
    avatarId: 1,
    role,
    owner: { id: 1 } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 3, examples: [] },
    ...extra,
  }
}

const CREW = team('cheese-core', '芝士核心组', 'OWNER')
const LAB = team('data-lab', '数据课第三组', 'MEMBER')
const OWN = team('linxia', '林夏', 'OWNER', { personal: true })

const SPACES = [{ id: 3, name: '数据分析课' }]

/** 目录这一件要的那一份值，缺的都当「没有」。 */
function navArgs(overrides: Record<string, unknown> = {}) {
  return {
    teams: [CREW, LAB],
    spaces: SPACES,
    editing: null,
    disbanding: null,
    transferring: null,
    transferCandidates: [] as TeamMember[],
    joinOpen: false,
    ...overrides,
  }
}

/** 能接手的两个人（外面已经去掉我自己）。 */
const CANDIDATES: TeamMember[] = [
  { role: 'ADMIN', user: { id: 7, username: 'xiaowang', nickname: '小王', avatarId: 0 } },
  { role: 'MEMBER', user: { id: 9, username: 'liulei', nickname: '刘磊', avatarId: 0 } },
] as TeamMember[]

export const HOME_ENTRIES: CatalogEntry[] = [
  // ---- 目录自己 ----------------------------------------------------------------
  {
    id: 'home-nav',
    title: 'HomeNav',
    about: '首页那一格的目录：待办、我所在的团队、我加入的空间。团队在原地展开成四样东西。',
    file: 'src/views/home/HomeNav.vue',
    component: HomeNav,
    needs: NAV_T,
    args: navArgs({ inbox: true }),
    states: [
      {
        name: '正在看「芝士核心组」',
        note: '`currentHandle` 说我现在在哪个团队里，那一行就一定是摊开的 —— 不然侧栏上找不到「你在这儿」。其余团队收着一行，点它才摊开（点在页面上试，摊开了就记进这台浏览器）。',
        props: { currentHandle: 'cheese-core' },
        expect: '芝士核心组',
      },
      {
        name: '有待我处理的事',
        note: '`awaiting` 是等我的件数，画在「待办」那一行的右端；0 或没给就不画那颗角标，一百以上收成 99+。',
        props: { awaiting: 128 },
        expect: '99+',
      },
      {
        name: '自己名下那几个项目',
        note: '自己名下那一个（`personal`）不当团队说：以自己的昵称单独一行，摆在「团队」小标题之上，展开后没有「成员」也没有「额度」，更没有那一行的 ⋯。',
        props: { teams: [OWN, CREW], currentHandle: 'linxia' },
        expect: '林夏',
      },
      {
        name: '手上一个空间都没加入',
        note: '空间那一栏只有一个小标题和「新建或加入团队」那一行 —— 空间是点进去才有的东西，没有也照样列得出这条路。',
        props: { spaces: [] },
        expect: '新建或加入团队',
      },
    ],
  },

  // ---- 目录的两端 --------------------------------------------------------------
  {
    id: 'home-sidebar-view',
    title: 'HomeSidebarView',
    about: '桌面首页那一格的侧栏：外面那只抽屉、「首页」那一行页头，和目录本体。',
    file: 'src/views/home/HomeSidebarView.vue',
    component: HomeSidebarView,
    needs: SIDEBAR_T,
    // 抽屉（`v-navigation-drawer`）只能长在布局里，预览站得把它放进 `<v-layout>`。
    layout: true,
    args: { ...navArgs({ currentHandle: 'cheese-core' }), mdAndUp: true },
    states: [
      {
        name: '桌面上常驻',
        note: '和手机整页是同一份目录、同一套值，只是这里坐在抽屉里，顶上多一行「首页」。手机上这一份整个不画（`mdAndUp` 为假），那一格的整页是 `HomeHubView`。',
        props: {},
        expect: '芝士核心组',
      },
    ],
  },
  {
    id: 'home-hub-view',
    title: 'HomeHubView',
    about: '手机底栏「首页」那一格的整页：一条能滚到底的整页，里面就是那份目录。',
    file: 'src/views/home/HomeHubView.vue',
    component: HomeHubView,
    needs: NAV_T,
    args: navArgs({ currentHandle: 'cheese-core' }),
    states: [
      {
        name: '手机上的整页',
        note: '比侧栏少一样东西：没有「待办」那一行 —— 手机底栏自己有一格是待办。页面底下留了 96px，最后一行不被底栏压住。',
        props: {},
        expect: '芝士核心组',
      },
    ],
  },

  // ---- 目录带的四个弹窗 ---------------------------------------------------------
  {
    id: 'join-space-dialog',
    title: 'JoinSpaceDialog',
    about: '用邀请码加入一个空间。加进去了就直接进那个空间 —— 留在原地等于什么都没发生。',
    file: 'src/views/home/JoinSpaceDialog.vue',
    component: JoinSpaceDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    args: { modelValue: true },
    states: [
      {
        name: '刚打开',
        note: '框里空着，邀请码由空间的创建者给。这个弹窗自己不知道后端，按一下「加入」只把码报上去；真去问、成了往哪跳在 `useJoinSpace`（首页那份目录和待办页共用那一份）。',
        props: {},
        expect: '邀请码由空间的创建者提供',
      },
      {
        name: '正在加入',
        note: '主按钮转圈，这时候按不动第二下。',
        props: { busy: true },
        expectSelector: '.v-btn--loading',
      },
      {
        name: '码不对、或者过期了',
        note: '上一回没成的理由由外面给回来，画在输入框下面；画面上这一格留着不清，人还在这里改码。',
        props: { error: '邀请码无效、已过期或已达使用上限' },
        expect: '邀请码无效、已过期或已达使用上限',
      },
    ],
  },
  {
    id: 'disband-team-dialog',
    title: 'DisbandTeamDialog',
    about: '解散团队前的二次确认：把团队名原样打一遍才按得下去，撤不回。',
    file: 'src/views/teams/DisbandTeamDialog.vue',
    component: DisbandTeamDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    args: { modelValue: true, team: CREW },
    states: [
      {
        name: '刚打开，名字还没打',
        note: '「解散团队」是红色的危险主操作，名字没打对之前一直是灰的。框里的字是框自己的状态，不从 props 来，所以「打对了名字」那一格要在框里自己打一遍。',
        props: {},
        expect: '输入团队名「芝士核心组」确认',
        expectSelector: '.v-btn--disabled',
      },
      {
        name: '正在解散',
        note: '解散本身在外面做（`useTeamNav`），这里只在打对名字之后报一声「确认了」。',
        props: { busy: true },
        expectSelector: '.v-btn--loading',
      },
      {
        name: '被后端拒了',
        note: '团队里还有没归档的项目时后端会拒，那句理由原样画在下面，弹窗不关 —— 人回去归档就能再来一次。',
        props: { error: '团队里还有没归档的项目，把它们都归档后才能解散' },
        expect: '团队里还有没归档的项目，把它们都归档后才能解散',
      },
    ],
  },
  {
    id: 'transfer-team-dialog',
    title: 'TransferTeamDialog',
    about: '把团队交给另一位成员：所有者退不掉团队，这里是他的一条出路。',
    file: 'src/views/teams/TransferTeamDialog.vue',
    component: TransferTeamDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    args: { modelValue: true, team: CREW, candidates: CANDIDATES },
    states: [
      {
        name: '名单来了',
        note: '名单是打开时在外面读的，而且已经去掉我自己 —— 不能把团队交给自己。挑中一个人「转让团队」才亮起来；挑哪一个是这一格的自己的状态，要试就在页面上点一下。',
        props: {},
        expect: '小王',
      },
      {
        name: '名单还在路上',
        note: '还没读到时什么都不说：「团队里还没有别的成员可以接手」是一句事实，不能在名单没到之前抢着说。',
        props: { candidates: [], loading: true },
        expect: '选一位成员接手这个团队。转让后 TA 是所有者，你成为管理员。',
      },
      {
        name: '团队里真的没有别人',
        note: '读完了、名单是空的，这时候才说这句话 —— 一个人也交不出去，只能去解散。',
        props: { candidates: [], loading: false },
        expect: '团队里还没有别的成员可以接手。',
      },
    ],
  },
  {
    id: 'team-profile-edit-dialog',
    title: 'TeamProfileEditDialog',
    about: '改团队对外的样子：名字、介绍、头像。地址、可见性、加入方式都不在这里。',
    file: 'src/views/teams/TeamProfileEditDialog.vue',
    component: TeamProfileEditDialog,
    needs: ['vuetify', 'i18n'],
    teleport: true,
    args: { modelValue: true, team: CREW },
    states: [
      {
        name: '刚打开',
        note: '照小队现在的样子起手；每次打开都重来一遍，上一次取消留下的半截输入不跟到下一次。保存本身（换头像、PATCH、撞名怎么落格）在 `useTeamNav`。',
        props: {},
        expect: '显示在侧栏团队名字下面，最多 250 字',
      },
      {
        name: '名字被别人占了',
        note: '撞名是人自己能修的（换一个名字就行），所以要落在名字那一格上，不能混成一句「保存失败」。',
        props: { nameError: '这个名称已被占用，换一个试试' },
        expect: '这个名称已被占用，换一个试试',
      },
      {
        name: '保存失败',
        note: '没网、没权限这类人自己修不了的，报在整张表单上。',
        props: { error: '保存失败，请稍后重试' },
        expect: '保存失败，请稍后重试',
      },
      {
        name: '正在保存',
        note: '保存时按钮转圈，弹窗关不掉 —— 上一次的请求还在路上。',
        props: { busy: true },
        expectSelector: '.v-btn--loading',
      },
    ],
  },
]
