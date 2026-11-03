/**
 * 预览站「题目板拆出来的那半张画面」这一组的第二批：`views/spaces/detail/` 里新拆出来的
 * 画面（`XView.vue` —— 只收 props、只发事件）。和 `catalogScenesSpaces.ts` 同一组，只是
 * 条目多了、`catalog.ts` 又顶到近一千行的上限，所以另起一份分册。规矩见 `catalog.ts`，
 * 数据见 `catalogScenesSpacesDetailFixtures.ts`。
 */
import type { UserRefTarget } from '@/lib/userRef'
import type { Task } from '@/types'
import type { CatalogEntry, CatalogNeed } from './catalog'

import { ANALYTICS_ALERTS } from './catalogScenesMisc2Fixtures'
import {
  ANALYTICS_PUBLISHER_OPTIONS,
  analyticsOverview,
  analyticsParticipants,
  analyticsPublishers,
  analyticsTask,
  announcement,
  JOIN_LINK,
  joinRequest,
  memberRow,
  publishedTask,
  task,
  templateDraft,
} from './catalogScenesSpacesDetailFixtures'

import AnalyticsExportButton from '@/views/spaces/detail/analytics/components/AnalyticsExportButton.vue'
import AnalyticsPublisherSelect from '@/views/spaces/detail/analytics/components/AnalyticsPublisherSelect.vue'
import OverviewView from '@/views/spaces/detail/analytics/OverviewView.vue'
import ParticipantsView from '@/views/spaces/detail/analytics/ParticipantsView.vue'
import PublishersView from '@/views/spaces/detail/analytics/PublishersView.vue'
import AnalyticsTasksView from '@/views/spaces/detail/analytics/TasksView.vue'
import AnnouncementsView from '@/views/spaces/detail/AnnouncementsView.vue'
import MembersView from '@/views/spaces/detail/MembersView.vue'
import SpaceJoinLinkCard from '@/views/spaces/detail/SpaceJoinLinkCard.vue'
import TasksView from '@/views/spaces/detail/TasksView.vue'
import TemplateFormView from '@/views/spaces/detail/TemplateFormView.vue'

/** 画面本来要装 Vuetify 和一套语言包。 */
const UI: CatalogNeed[] = ['vuetify', 'i18n']

/** 成员页的画面里「邮箱域名组」那颗按钮交给路由解析，所以场景里也把路由装上，落点是空白的通配页。 */
const UI_TR: CatalogNeed[] = [...UI, 'router']

/** 模板表单回列表那一条（`SpacesDetailSettingsTemplates`）：画面只把它透给 NavLink。 */
const LIST_ROUTE = { name: 'SpacesDetailSettingsTemplates', params: { spaceId: 8 } }

/** 题目列表每一行点去哪（`Tasks.vue` 里现算的正是这一条）：画面只把它透给 `TaskRow`。 */
function rowTo(task: Task): UserRefTarget {
  return { name: 'TasksDetail', params: { spaceId: 8, taskId: task.id } }
}

export const SCENES_SPACES_DETAIL_ENTRIES: CatalogEntry[] = [
  {
    id: 'analytics-publisher-select',
    title: 'AnalyticsPublisherSelect',
    about: '分析页的发布者筛选控件：候选与加载状态由页面传入，选择通过 v-model 发回；独立预览不请求服务。',
    file: 'src/views/spaces/detail/analytics/components/AnalyticsPublisherSelect.vue',
    component: AnalyticsPublisherSelect,
    needs: UI,
    args: { modelValue: null, publishers: [], loading: false },
    states: [
      {
        name: '全部发布者',
        note: '没有选择任何发布者，仍能显示全部这一项。',
        props: {},
        expectSelector: '[role="combobox"]',
      },
      {
        name: '选中发布者',
        note: '页面提供的候选可作为当前选择显示。',
        props: { modelValue: 8, publishers: [{ title: '课程管理员', value: 8 }] },
        expect: '课程管理员',
      },
      {
        name: '正在读取',
        note: '候选正在获取时字段上多出一道加载条纹，当前选择不变。',
        props: { loading: true },
        expectSelector: '.v-field--loading',
      },
    ],
  },
  {
    id: 'announcements-view',
    title: 'AnnouncementsView',
    about:
      '「空间公告」那一页的画面：公告卡片那一列、收在「已到期」那一折里的过期几条、发布/编辑那个对话框。发、改、删、置顶都 emit 给容器 `Announcements.vue`；只有管理员看得到那些操作。',
    file: 'src/views/spaces/detail/AnnouncementsView.vue',
    component: AnnouncementsView,
    needs: UI,
    args: {
      current: [],
      expired: [],
      loaded: true,
      isManager: false,
      submitting: false,
      notifyCount: 0,
      failed: false,
      errorReason: null,
      forbidden: false,
      open: false,
    },
    states: [
      {
        name: '一条公告都没有',
        note: '读回来是空的：就地说一句「暂无公告」，不是一块空白。',
        props: {},
        expect: '暂无公告',
      },
      {
        name: '列着公告',
        note: '管理员看得到发布那颗按钮，卡片上带置顶/编辑/删除那几个动作。',
        props: { current: [announcement(), announcement({ id: 2, title: '考试提醒', pinned: true })], isManager: true },
        expect: '学期安排',
      },
      {
        name: '读失败',
        note: '一条都没读到、而且是读失败了：换成错误那一块。读到过的话不会走这里。',
        props: { failed: true, errorReason: '加载公告失败' },
        expect: '加载公告失败',
      },
    ],
  },
  {
    id: 'members-view',
    title: 'MembersView',
    about:
      '「成员与角色」那一页的画面：页头（「邮箱域名组」指向设置里那一页）、加入方式那张卡（加入链接、「加入需要批准」）、开着批准时的加入申请、搜索框、那张成员表（名字、角色的一句话说明），改角色的按钮按所有者 / 管理员 / 成员分档。读名单、读写链接、批准申请、改角色、确认框都在容器 `Members.vue` 里。',
    file: 'src/views/spaces/detail/MembersView.vue',
    component: MembersView,
    needs: UI_TR,
    args: {
      spaceId: 8,
      rows: [],
      isOwner: true,
      busy: false,
      failed: false,
      errorDetail: null,
      joinLink: JOIN_LINK,
      joinBusy: false,
      joinError: '',
      joinCopied: false,
      requests: [],
      requestsLoading: false,
      requestsFailed: false,
      requestsError: null,
      answering: null,
      answerError: '',
    },
    states: [
      {
        name: '一个成员都没有',
        note: '名单空着：就地说一句「暂无成员」，不是一块空白。',
        props: {},
        expect: '暂无成员',
      },
      {
        name: '列着成员',
        note: '一行普通成员、一行管理员：所有者才看得到「设为管理员」那颗按钮。',
        props: {
          rows: [memberRow(), memberRow({ userId: 12, handle: 'bo', name: '波波', role: 'ADMIN' })],
        },
        expect: '共 2 人',
      },
      {
        name: '读失败',
        note: '名单没读出来：错误替换整个列表区，不落进「暂无成员」—— 那会让失败看着像本来就没人。',
        props: { failed: true, errorDetail: '加载成员失败' },
        expect: '加载成员失败',
      },
      {
        name: '开着批准，有人在等',
        note: '「加入需要批准」开着：成员表上面多一块加入申请，每份写着是谁、什么时候申请的，拒绝 / 批准两颗。',
        props: { joinLink: { ...JOIN_LINK, approval: true }, requests: [joinRequest()] },
        expect: '林同学',
      },
      {
        name: '开着批准，没人在等',
        note: '开关开着、一份申请都没有：那一块还在，说一句「暂无加入申请」。',
        props: { joinLink: { ...JOIN_LINK, approval: true } },
        expect: '暂无加入申请',
      },
    ],
  },
  {
    id: 'space-join-link-card',
    title: 'SpaceJoinLinkCard',
    about:
      '空间成员页上「加入方式」那张卡：这个空间唯一的那条加入链接（复制、重置，重置后旧链接立即失效），和「加入需要批准」的开关。读链接、重置、切换、复制都由页面（`Members.vue`）做，卡只吃数据、往上报动作。和团队那张 `TeamJoinLinkCard` 同一个形状。',
    file: 'src/views/spaces/detail/SpaceJoinLinkCard.vue',
    component: SpaceJoinLinkCard,
    needs: UI,
    args: { link: JOIN_LINK, busy: false, error: '', copied: false },
    states: [
      {
        name: '链接在，直接加入',
        note: '读回来了：链接一格只读，旁边复制和重置；开关关着，拿到链接的人登录后直接成为成员。',
        props: {},
        expect: '复制链接',
      },
      {
        name: '刚复制过',
        note: '按钮上的字换成「已复制」，别处不变。',
        props: { copied: true },
        expect: '已复制',
      },
      {
        name: '还在读',
        note: '链接还没回来：一条进度线，开关那一行不画。',
        props: { link: null, busy: true },
        expectSelector: '.v-progress-linear',
      },
      {
        name: '上一次没做成',
        note: '重置、切换或复制失败：那一句画在卡顶上，链接和开关照旧在。',
        props: { error: '操作失败，稍后重试' },
        expect: '操作失败，稍后重试',
      },
    ],
  },
  {
    id: 'template-form-view',
    title: 'TemplateFormView',
    about:
      '「题目模板」下一级那张整页表单的画面：左上角回模板列表，下面几摞字段（名称/描述/题目标题、题目默认设置、题目描述编辑器）绑在一份草稿上。认路、读模板、真保存都在容器 `TemplateForm.vue` 里；画面只收 `initial` 草稿、发 `submit`。',
    file: 'src/views/spaces/detail/TemplateFormView.vue',
    component: TemplateFormView,
    // 画面里「回模板列表」那一条是 `NavLink`，表单里那颗 `BaseButton` 也指着 `listRoute`：
    // 两处都按 name 解析。演示页（`DemoCatalog.vue`）始终装的是 `demoRouter`，所以那条
    // name 得在 `demoRouter.catalogTargets` 里注册（已注册 `SpacesDetailSettingsTemplates`）；
    // 不装路由只会让这一格的 spec 漏报，护不住真页面。
    needs: UI_TR,
    args: { initial: templateDraft(), isEditing: false, listRoute: LIST_ROUTE },
    states: [
      {
        name: '新建一张模板',
        note: '空表单：标题是「创建题目模板」，各字段空着，底下取消 / 保存两颗。',
        props: { initial: templateDraft({ name: '', description: '', title: '' }), isEditing: false },
        expect: '创建题目模板',
      },
      {
        name: '改一张已有的模板',
        note: '带值进来：标题变成「编辑题目模板」，名称那一格摆着读回来的名字。',
        props: {
          initial: templateDraft({ name: '团队题模板', submitterType: 'TEAM', rank: 2 }),
          isEditing: true,
        },
        expect: '编辑题目模板',
      },
    ],
  },
  {
    id: 'tasks-view',
    title: 'TasksView',
    about:
      '「题目列表」那一页的画面：顶上那栏置顶公告、范围/话题/搜索/排序那一条筛选、普通题目列表，以及「我发布的」那一格（带审核状态、读失败与空状态）。筛选项、分页、读「我发布的」、以及每一行点去哪，都在容器 `Tasks.vue` 里算好传进来；画面只收 props、发事件。',
    file: 'src/views/spaces/detail/TasksView.vue',
    component: TasksView,
    needs: UI_TR,
    args: {
      scope: 'all',
      pendingOnly: false,
      hotTopics: [
        { id: 1, name: '动态规划' },
        { id: 2, name: '图论' },
      ],
      categoryFilterOptions: [
        { title: '全部题目', value: null },
        { title: '第 1 章', value: 7 },
      ],
      spaceId: 8,
      announcements: [announcement({ id: 1, title: '本周答疑改到周四', pinned: true })],
      announcementsTarget: { name: 'SpacesAnnouncements', params: { spaceId: 8 } },
      rowTo,
      selectedCategory: null,
      selectedTopics: [],
      sort: 'latestPublished',
      search: '',
      loadingMore: false,
      hasMore: false,
      refreshing: false,
      tasks: [task()],
      total: 1,
      mineTasks: [],
      mineTotal: 0,
      mineHasMore: false,
      mineLoadingMore: false,
      closedTasks: [],
      closedTotal: 0,
      closedHasMore: false,
      closedLoadingMore: false,
      closedOpen: false,
      publishedLoading: false,
      publishedFailed: false,
      publishedError: null,
      publishedTasks: [],
    },
    states: [
      {
        name: '一栏题目',
        note: '顶上是那栏置顶公告，下面是筛选条和题目列表；每一行的去处由容器算好传进来。',
        props: {},
        expect: '最短路径',
      },
      {
        name: '我发布的',
        note: '范围切到「我发布的」：换成带审核状态的卡片，右边写着上没上板、有几件事等着处理。',
        props: { scope: 'publishing', publishedTasks: [publishedTask()] },
        expect: '排课系统',
      },
      {
        name: '我发布的读失败',
        note: '「我发布的」没读出来：错误替换掉这一格，不落进「暂无题目」—— 那会让失败看着像本来就还没发过。',
        props: { scope: 'publishing', publishedFailed: true, publishedError: '网络错误' },
        expect: '无法加载题目',
      },
      {
        name: '已截止的收在末尾',
        note: '「全部」先列还能领的；已截止或名额已满的收成末尾一行，写着有几道，点开才列出来。',
        props: {
          // 截止时间已过：折进这一栏的题目，状态写的是「已截止」。
          closedTasks: [task({ id: 2, name: '上学期的期末项目', deadline: 1751212800000 })],
          closedTotal: 1,
          closedOpen: true,
        },
        expectSelector: '[data-testid="tasks-closed"]',
      },
      {
        name: '我参与的在最上面',
        note: '「全部」最上面一组「我参与的」：我手上有一份的题，不收起；每一行写我这一份的状态和我自己的提交截止，右边不写题目的状态。',
        props: {
          mineTasks: [
            task({
              id: 3,
              name: '动态规划入门',
              startMode: 'APPLY',
              joined: true,
              submittable: true,
              // 2030 年：截图哪天拍都还没到，这一行写「在做」。
              userDeadline: 1893456000000,
            }),
          ],
          mineTotal: 1,
        },
        expectSelector: '[data-testid="tasks-mine"]',
      },
    ],
  },
  {
    id: 'analytics-export-button',
    title: 'AnalyticsExportButton',
    about:
      '分析页每一格右上那颗「导出」：只画按钮、只报「点了导出」。拉 CSV 要带登录凭据，那是页面的事（`composables/useAnalyticsExport.ts`）。',
    file: 'src/views/spaces/detail/analytics/components/AnalyticsExportButton.vue',
    component: AnalyticsExportButton,
    needs: UI,
    args: { label: '导出题目清单', loading: false },
    states: [
      {
        name: '默认',
        note: '页面给什么字就写什么字，按一下报 `export`。',
        props: {},
        expect: '导出题目清单',
      },
      {
        name: '正在导出',
        note: '导出还没回来：按钮自己转起来。',
        props: { loading: true },
        expectSelector: '.v-btn__loader',
      },
    ],
  },
  {
    id: 'overview-view',
    title: 'OverviewView',
    about:
      '管理员看板「总览」那一格的画面：六个读数、按分类/审核/完成的分布、两条趋势，外加一组能点的待处理数。取数、跳转都在页面 `Overview.vue` 里，这里只吃数据、往上报动作。',
    file: 'src/views/spaces/detail/analytics/OverviewView.vue',
    component: OverviewView,
    needs: UI,
    args: {
      loading: false,
      failed: false,
      errorDetail: null,
      overview: analyticsOverview(),
      alerts: ANALYTICS_ALERTS,
      publishers: ANALYTICS_PUBLISHER_OPTIONS,
      publishersLoading: false,
      publisherId: null,
      groupBy: 'week',
    },
    states: [
      {
        name: '一栏总览',
        note: '读回来了：六个读数摆在最上，下面三张分布和两条趋势。',
        props: {},
        expect: '题目总数',
      },
      {
        name: '正在读取',
        note: '第一次还没读回来：一条进度条，不去画「暂无数据」。',
        props: { loading: true, overview: null, alerts: null },
        expectSelector: '.v-progress-linear',
      },
      {
        name: '读失败',
        note: '这一次没读到：换成错误那一块，服务端的原话摆出来 —— 不落进「暂无数据」。',
        props: { failed: true, errorDetail: '统计接口 500', overview: null, alerts: null },
        expect: '统计接口 500',
      },
      {
        name: '一条数据都没有',
        note: '读回来是空的：就地说一句「暂无总览数据」，不是一块空白。',
        props: { overview: null, alerts: null },
        expect: '暂无总览数据',
      },
    ],
  },
  {
    id: 'participants-view',
    title: 'ParticipantsView',
    about:
      '管理员看板「参与者」那一格的画面：几个读数、三条趋势、六张分布（审核、完成、实名、年级、专业、班级），右上那颗导出。取数与导出在页面 `Participants.vue` 里，这里只吃数据、往上报动作。',
    file: 'src/views/spaces/detail/analytics/ParticipantsView.vue',
    component: ParticipantsView,
    needs: UI,
    args: {
      loading: false,
      failed: false,
      errorDetail: null,
      participants: analyticsParticipants(),
      publishers: ANALYTICS_PUBLISHER_OPTIONS,
      publishersLoading: false,
      exporting: false,
      publisherId: null,
      participationApproved: null,
      completionStatus: null,
      realName: 'all',
      groupBy: 'week',
    },
    states: [
      {
        name: '一栏参与者数据',
        note: '读回来了：读数、趋势和六张分布都摆出来。',
        props: {},
        expect: '申请/开始主体数',
      },
      {
        name: '正在读取',
        note: '第一次还没读回来：一条进度条，不去画「暂无参与者数据」。',
        props: { loading: true, participants: null },
        expectSelector: '.v-progress-linear',
      },
      {
        name: '读失败',
        note: '这一次没读到：换成错误那一块，服务端的原话摆出来。',
        props: { failed: true, errorDetail: '统计接口超时', participants: null },
        expect: '统计接口超时',
      },
      {
        name: '一条数据都没有',
        note: '读回来是空的：就地说一句「暂无参与者数据」，不是一块空白。',
        props: { participants: null },
        expect: '暂无参与者数据',
      },
    ],
  },
  {
    id: 'publishers-view',
    title: 'PublishersView',
    about:
      '管理员看板「出题人」那一格的画面：三个榜首（发题最多、成功率最高、申请/开始最多）加一张出题人明细表，右上那颗导出。取数与导出在页面 `Publishers.vue` 里，这里只吃数据、往上报动作。',
    file: 'src/views/spaces/detail/analytics/PublishersView.vue',
    component: PublishersView,
    needs: UI,
    args: {
      loading: false,
      failed: false,
      errorDetail: null,
      publishers: analyticsPublishers(),
      exporting: false,
      sortBy: 'taskCount',
      sortOrder: 'desc',
    },
    states: [
      {
        name: '一栏出题人',
        note: '读回来了：三张榜首在最上，下面是明细表，一行一个出题人。',
        props: {},
        expect: '李老师',
      },
      {
        name: '正在读取',
        note: '第一次还没读回来：表里先摆骨架行，不去画「暂无出题人」。',
        props: { loading: true, publishers: [] },
        expectSelector: '.agrid__bone',
      },
      {
        name: '读失败',
        note: '这一次没读到：换成错误那一块，服务端的原话摆出来。',
        props: { failed: true, errorDetail: '统计接口超时', publishers: [] },
        expect: '统计接口超时',
      },
      {
        name: '一个出题人都没有',
        note: '读回来是空的：表里写一句「暂无出题人」，不是一块空白。',
        props: { publishers: [] },
        expect: '暂无出题人',
      },
    ],
  },
  {
    id: 'analytics-tasks-view',
    title: 'TasksView',
    about:
      '管理员看板「题目」那一格的画面：一串筛选（出题人、排序、两个待办开关）加一张题目明细表，右上那颗导出。取数、导出、写回地址都在页面 `Tasks.vue` 里，这里只吃数据、往上报动作。',
    file: 'src/views/spaces/detail/analytics/TasksView.vue',
    component: AnalyticsTasksView,
    needs: UI,
    args: {
      loading: false,
      failed: false,
      errorDetail: null,
      tasks: [analyticsTask(), analyticsTask({ taskId: 102, taskName: '排课系统', pendingReviewCount: 0 })],
      publishers: ANALYTICS_PUBLISHER_OPTIONS,
      publishersLoading: false,
      exporting: false,
      publisherId: null,
      sortBy: 'createdAt',
      sortOrder: 'desc',
      hasPendingApproval: false,
      hasPendingReview: false,
    },
    states: [
      {
        name: '一栏题目',
        note: '读回来了：筛选条在最上，下面一行一个题目，带审核状态和几个计数。',
        props: {},
        expect: '最短路径',
      },
      {
        name: '翻上「只看待审核提交」',
        note: '那颗开关是按下的：勾上，页面会把筛选写回地址再取一次。',
        props: { hasPendingReview: true },
        expectSelector: '.mdi-checkbox-marked',
      },
      {
        name: '读失败',
        note: '这一次没读到：换成错误那一块，服务端的原话摆出来。',
        props: { failed: true, errorDetail: '统计接口超时', tasks: [] },
        expect: '统计接口超时',
      },
      {
        name: '一道题都没有',
        note: '读回来是空的：表里写一句「暂无题目」，不是一块空白。',
        props: { tasks: [] },
        expect: '暂无题目',
      },
    ],
  },
]
