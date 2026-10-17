/**
 * 预览站里「路由页拆出来的那半张画面」这一组：几个整页各拆成一个页面（`X.vue` ——
 * 读地址、取数、跳转）加一个画面（`XView.vue` —— 只收 props、只发事件）。这里摆的是
 * 画面那一半，所以每一件都不带路由、不带接口就能单独渲染。
 *
 * 这一组有四件：空间名录（谁都能看的那一页）、小队链接落地页、小队里的项目列表，以及
 * 提问页。条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到将近一千行的上限。这里的
 * `CatalogEntry` 是 type-only 引用，`catalog.ts` 反过来要 `SCENES_PAGES_ENTRIES` 这个值，
 * 运行时不构成循环。数据见 `catalogScenesPagesFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { INDEX_APPLICATIONS, INDEX_SPACES, noop, noopAsync, TEAM_PROJECTS } from './catalogScenesPagesFixtures'
import { topicCandidates } from './catalogScenesSpacesFixtures'
import { teamProfile } from './catalogSharedFixtures'

import AskView from '@/views/question/AskView.vue'
import IndexView from '@/views/spaces/IndexView.vue'
import TeamInviteViewView from '@/views/TeamInviteViewView.vue'
import TeamProjectsView from '@/views/teams/detail/TeamProjectsView.vue'

/** 四件都要 Vuetify 的画法和一套语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 空间名录和小队链接落地页里带着 `to` 的去处（进某个空间、进小队主页）按 name 指路，
 *  画面自己没有路由就当场抛，所以这两件要多装 router —— 名字都在 `demoRouter.ts` 里。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 标题 / 话题 / 悬赏三个字段的 Vuetify 状态位：默认都不报错。 */
const OK_FIELD = { 'error-messages': [] as string[], error: false }

export const SCENES_PAGES_ENTRIES: CatalogEntry[] = [
  {
    id: 'spaces-index-view',
    title: 'IndexView',
    about:
      '空间名录那一页的画面：顶上一条「我的空间申请」（等审 / 驳回 / 过审各一条自己的路），下面是所有公开空间的名录，登录的人还能当场新建一个。取数、翻页、建空间调后端都在容器 `spaces/Index.vue` 里；「登录了没有」由容器以 `loggedIn` 传进来，画面自己不认账号服务。',
    file: 'src/views/spaces/IndexView.vue',
    component: IndexView,
    needs: UI_T_R,
    // 建空间的对话框画在 body 上，不在容器里。
    teleport: true,
    args: {
      mdAndUp: true,
      loggedIn: false,
      applicationsError: false,
      applications: [],
      applicationOffset: 0,
      firstRun: false,
      spacesFailed: false,
      error: null,
      refresh: noopAsync,
      hasMore: false,
      loadingMore: false,
      refreshing: false,
      spaces: [],
      total: 0,
      loadMore: noopAsync,
      resubmittingId: null,
      creating: false,
      existingAvatarId: undefined,
      createError: '',
      createdInviteCode: null,
      createDialog: false,
      spaceName: '',
      selectedAvatar: undefined,
      spaceIntro: '',
      codeDialog: false,
    },
    states: [
      {
        name: '名录上还没有空间',
        note: '一块都没有：名录那格里说一句「暂无空间」，不是一片空白。',
        props: {},
        expect: '暂无空间',
      },
      {
        name: '名录上有空间',
        note: '六块板，各写着名字和简介，整块可点进它的题目列表。',
        props: { spaces: INDEX_SPACES, total: 6 },
        expect: '操作系统',
      },
      {
        name: '读失败',
        note: '名录没读出来：错误替换整块名录（`spaces` 是空的，光看「空」分不出读失败和本来就空）。',
        props: { spacesFailed: true, error: new Error('网络不通') },
        expect: '加载空间列表失败',
      },
      {
        name: '第一次来',
        note: '一个项目都没有的人：顶上多一格「从这里开始」，把他往建项目那条路上领。',
        props: { firstRun: true, loggedIn: true },
        expect: '从这里开始',
      },
      {
        name: '我的空间申请',
        note: '三条自己的申请：等审的、被驳回带原因的（只有它能改一版再来）、过审的（能直接进去）。',
        props: { loggedIn: true, applications: INDEX_APPLICATIONS },
        expect: '修改并重新提交',
      },
      {
        name: '新建空间',
        note: '「新建空间」点开：头像、名字、简介三个字段，名字空着时主操作按不动。',
        props: { loggedIn: true, createDialog: true },
        expect: '空间名称',
      },
      {
        name: '刚建好，给你邀请码',
        note: '后端在建空间那一刻就发了邀请码，别处看不到：这里当场交到人手上，关掉就进这个空间。',
        props: { loggedIn: true, codeDialog: true, createdInviteCode: 'CHZ-2026-9F3A' },
        expect: '这是你的邀请码',
      },
    ],
  },
  {
    id: 'team-invite-view-view',
    title: 'TeamInviteViewView',
    about:
      '小队链接落地页的画面：有人拿着一串小队自己的链接（`/team-invites/:token`）过来，没加入的人看到的就是这一页 —— 小队长什么样、要不要加入。令牌换小队、加入调后端、没登录先送去登录，都在容器 `TeamInviteView.vue` 里。',
    file: 'src/views/TeamInviteViewView.vue',
    component: TeamInviteViewView,
    needs: UI_T_R,
    args: { team: null, join: noopAsync, needsLogin: false, error: '', busy: false, invalid: false },
    states: [
      {
        name: '链接有效，等你加入',
        note: '小队开着审批：先看一眼它是谁、谁建的、多少人，再决定，加入要写一句理由（选填）。',
        props: { team: teamProfile() },
        expect: '申请加入',
      },
      {
        name: '我已经在这个小队里',
        note: '成员再点一次链接：不摆加入的按钮，直接给一条进去的路。',
        props: { team: teamProfile({ joinStatus: 'member' }) },
        expect: '进入团队',
      },
      {
        name: '还没登录',
        note: '先登录：这一页不摆小队的任何东西，只说登录后能看到什么。',
        props: { needsLogin: true },
        expect: '登录后可查看团队信息，再决定是否加入',
      },
      {
        name: '链接失效了',
        note: '令牌不是这个小队的（改过、撤销过）：说清该找谁要新链接，不给重试 —— 再点一次还是同一条链接。',
        props: { invalid: true, error: '团队链接已失效，请向团队管理员索取新链接' },
        expect: '团队链接已失效，请向团队管理员索取新链接',
      },
      {
        name: '读失败',
        note: '令牌对，但小队信息没读出来：留在这里给一条重试的路，不退回「找不到」。',
        props: { error: '团队信息没读出来' },
        expect: '团队信息没读出来',
      },
      {
        name: '正在读',
        note: '换小队还在路上：一条进度线，底下什么都不摆。',
        props: { busy: true },
        expectSelector: '[aria-label="加载中"]',
      },
    ],
  },
  {
    id: 'team-projects-view',
    title: 'TeamProjectsView',
    about:
      '小队的项目列表那一页的画面：一条条项目卡片（名字、建的时间），点一张进这个项目，右键弹那一张自己的操作。取数、跳转、右键菜单在容器 `teams/detail/TeamProjects.vue` 里 —— 画面只吃 `projects` 和两个回调。',
    file: 'src/views/teams/detail/TeamProjectsView.vue',
    component: TeamProjectsView,
    needs: UI_T,
    args: {
      teamData: teamProfile(),
      loading: false,
      projects: [],
      // 右键菜单开着没有、弹在哪，是容器的状态（`useRowMenu`）。预览站上一直收着。
      rowMenu: { open: noop, bind: () => ({ modelValue: false, point: null, 'onUpdate:modelValue': noop }) },
      // 那一张卡片自己那几条操作由容器按项目算（改名、归档……），菜单收着时用不上。
      projectMenu: () => [],
      fmtDate: (iso: string) => iso.slice(0, 10),
      error: null,
    },
    states: [
      {
        name: '小队里有项目',
        note: '三张卡片，各写着名字和建的时间，点一张进这个项目。',
        props: { projects: TEAM_PROJECTS },
        expect: '机器学习入门',
      },
      {
        name: '还没有项目',
        note: '一个都没有：留住这一格，摆一句「还没有项目」和一条新建的路，不是一片空白。',
        props: {},
        expect: '还没有项目',
      },
      {
        name: '读失败',
        note: '没读出来：顶上一条能关掉的错误，「空」那一路不摆 —— 读失败和本来就没有是两回事。',
        props: { error: '加载项目失败' },
        expect: '加载项目失败',
      },
      {
        name: '正在读',
        note: '还在路上：一颗转着的圈，底下什么都不摆。',
        props: { loading: true },
        expectSelector: '.v-progress-circular',
      },
    ],
  },
  {
    id: 'ask-view',
    title: 'AskView',
    about:
      '提问页的画面：一个标题输入、一块正文编辑器、一只话题选择器，下面是「加悬赏」那一行和发布按钮，右手边钉着「提问的艺术」。校验、发布、搜索与新建话题都在容器 `question/Ask.vue` 里 —— 编辑器实例和三个字段的报错都由容器以 props 传进来。',
    file: 'src/views/question/AskView.vue',
    component: AskView,
    needs: UI_T,
    args: {
      titleProps: OK_FIELD,
      editorConfig: {},
      topicsProps: OK_FIELD,
      // 话题的搜索与新建归容器（那里才认得接口）；这里给一份够用的候选。
      searchTopics: async (query: string) => topicCandidates(query),
      createTopic: async () => 99,
      hasBounty: false,
      bountyProps: OK_FIELD,
      isSubmitting: false,
      submit: async () => undefined,
      title: '',
      topics: [],
      bounty: 1,
    },
    states: [
      {
        name: '刚进来',
        note: '空的标题、空的正文、没选话题：发布按钮在这儿，右手边是「提问的艺术」。',
        props: {},
        expect: '发布问题',
      },
      {
        name: '加了悬赏',
        note: '点一下「添加悬赏」：滑杆摊开，右上写着这条题挂几个知是币，按钮自己变成「取消悬赏」。',
        props: { hasBounty: true, bounty: 5 },
        expect: '取消悬赏',
      },
      {
        name: '校验没过',
        note: '标题这一格当场报错（空标题、或结尾不是问号），错在哪儿就写在那一格下面。',
        props: { titleProps: { 'error-messages': ['填写问题标题'], error: true } },
        expect: '填写问题标题',
      },
      {
        name: '正在发布',
        note: '发布按钮转起来了：这一下是发得出去的，别让人再点一次。',
        props: { isSubmitting: true },
        expect: '发布问题',
      },
    ],
  },
]
