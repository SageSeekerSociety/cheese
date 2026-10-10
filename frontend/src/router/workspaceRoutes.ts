// 项目工作台 (P0 架构): ONE frame, everything else inside it.
//
// These used to be eight sibling routes — the workspace was simply the one that
// happened to render a sidebar, so opening 总览 unmounted it, and every page out
// there had to hand-roll a 返回 button back to a frame it was never inside.
// Now `/projects/:projectId` IS the frame: `ProjectSidebar` rides the app-wide
// `sidebar` named view (the same mechanism 首页/空间/设置 use) and stays put
// across every navigation below, so a click in the sidebar changes only the
// content area — the one rule that makes a click's outcome predictable.
//
// What the frame shows is entirely in the URL: which topic, which DM, which
// project doc. 私聊 and 项目文档 used to live only in component state, so
// neither could be shared, refreshed, or navigated back to.
//
// This lives in its own module so the spec next door exercises THESE records
// rather than a restatement of them (same reason as ./legacyProjectPaths).
import type { RouteRecordRaw } from 'vue-router'

import { addressProps } from '@/lib/addresses'

// 项目文档 used to be three routes distinguished by NAME rather than by a
// parameter, which is how the same words ended up leading to two different
// places: the sidebar swapped the panel in place, the action card pushed the
// full page. One address per document now; the old links still resolve.
const DOC_KINDS = ['charter', 'weeklies'] as const

// 地址是给人看的：项目用短名，频道、任务、资料库文档用项目里的编号
// （`/projects/cheese/tasks/318`）。参数在地址上是短名和编号，页面拿到的 props 是
// UUID（`addressProps`）；代码照旧拿 UUID 拼路由，落地时由 `canonicalAddress` 换成
// 短的那一种。数字或 UUID 都认，所以编号参数的正则两样都收。
const NUMBER_OR_ID = '(\\d+|[0-9a-fA-F-]{36})'

// 频道和任务以前在 `topics/<频道>` 下面。发出去的旧链接原样换到新地址。
const LEGACY_TOPIC_PATHS: RouteRecordRaw[] = [
  {
    path: 'topics/:topicId',
    redirect: (to) => ({ name: 'workspace-topic', params: to.params, query: to.query, hash: to.hash }),
  },
  {
    path: 'topics/:topicId/threads/:threadId',
    redirect: (to) => ({ name: 'workspace-thread', params: to.params, query: to.query, hash: to.hash }),
  },
  {
    path: 'topics/:topicId/tasks/:taskId',
    redirect: (to) => ({
      name: 'workspace-task',
      params: { projectId: to.params.projectId, taskId: to.params.taskId },
      query: to.query,
      hash: to.hash,
    }),
  },
]

// The topic list is the mobile workspace destination. Child pages declare their
// parent through backTo, which is used by both desktop and mobile navigation.
export const workspaceRoutes: RouteRecordRaw = {
  path: '/projects/:projectId',
  components: {
    default: () => import('@/views/workspace/ProjectShell.vue'),
    sidebar: () => import('@/views/workspace/ProjectSidebar.vue'),
  },
  props: { default: addressProps, sidebar: addressProps },
  // `projectFrame` 标出「项目这个框」。没有浏览历史可退时，顶栏那颗 ← 靠它认出
  // 自己站在项目的根上，退到项目所属的小队。
  // 顶栏标题是这个项目的名字（ProjectShell 按 `project-frame` 这个键填进来），不是
  // 一句对哪个项目都一样的「项目工作台」：顶栏回答的是「我在哪」，各页的页头回
  // 答「这一页是什么」。名字到货之前先用这句兜底。
  meta: {
    titleKey: 'navigation.pages.projectWorkspace',
    dynamicTitleKey: 'project-frame',
    isFullPage: true,
    projectFrame: true,
  },
  children: [
    {
      name: 'workspace-project',
      path: '',
      component: () => import('@/views/workspace/WorkspaceEntry.vue'),
      props: addressProps,
      // 手机上这一层就是话题列表，项目名 + 切换器由它自己填进顶栏（barSlot）。
      // 它同时是底栏「工作区」那一格的落点，所以底栏留着，也没有"回上一层"。
      meta: { barSlot: true },
    },
    {
      name: 'workspace-topic',
      path: `channels/:topicId${NUMBER_OR_ID}`,
      component: () => import('@/views/workspace/TopicView.vue'),
      props: addressProps,
      // 手机上这是页面栈的末端：底栏收起（它不是一级目的地），← 回到话题列表。
      // `barSlot`: TopicHeader（标题 + #id + 阶段）填的就是顶栏那一格，不再自己
      // 画一条横条；← 由顶栏按 backTo 出。
      meta: { hideTabs: true, backTo: 'workspace-project', barSlot: true },
    },
    {
      // 一条支线：频道主线上一条消息下面的回复。和频道页是同一个组件——桌面上频道
      // 主线还在左边，支线占右边那一半；手机上支线是一整页，← 回到频道。
      name: 'workspace-thread',
      path: `channels/:topicId${NUMBER_OR_ID}/threads/:threadId`,
      component: () => import('@/views/workspace/TopicView.vue'),
      props: addressProps,
      meta: { hideTabs: true, backTo: 'workspace-topic' },
    },
    {
      // 一个频道的全部任务就是项目的全部任务带上这个频道的筛选：发出去的旧地址落到那里。
      path: 'topics/:topicId/tasks',
      redirect: (to) => ({
        name: 'project-tasks',
        params: { projectId: to.params.projectId },
        query: { channel: String(to.params.topicId) },
      }),
    },
    {
      // 任务页：一个任务自己的对话和实况文档。和房间页是同一个组件——任务挂在房间下，
      // 房间要先打开，任务页借它的名册和外框；props 里多出来的 taskId 决定画哪一边。
      // 地址里只有任务的编号，它所在的频道由 `addressProps` 补上。
      name: 'workspace-task',
      path: `tasks/:taskId${NUMBER_OR_ID}`,
      component: () => import('@/views/workspace/TopicView.vue'),
      props: addressProps,
      meta: { hideTabs: true, backTo: 'workspace-project', barSlot: true },
    },
    {
      // 项目总览：进项目落在这一页（壳的 `home`）。它答的是「这个项目怎么样了」：项目
      // 总览那份文档、最近进展、谁在做什么、做出了什么。「需要我处理」不在这里另列一
      // 份，只在首页「待办」。
      name: 'workspace-overview',
      path: 'overview',
      component: () => import('@/views/workspace/ProjectOverview.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.overview',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.overview', icon: 'mdi-view-dashboard-outline' },
      },
    },
    {
      // 全部任务：项目里的任务按状态分组列出来。项目总览和频道下面那一行「全部任务」
      // 都进这里，后者在地址上带着 `?channel=`。
      name: 'project-tasks',
      path: 'tasks',
      component: () => import('@/views/workspace/ProjectTasks.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.tasks',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.tasks', icon: 'mdi-format-list-checks' },
      },
    },
    {
      // 看板退役了：「这个项目怎么样了」在项目总览，「有哪些任务」在全部任务。发出去
      // 的旧地址落到总览。
      path: 'running',
      redirect: (to) => ({ name: 'workspace-overview', params: { projectId: to.params.projectId } }),
    },
    {
      name: 'workspace-dm',
      path: 'dm/:peer',
      component: () => import('@/views/workspace/DmView.vue'),
      props: addressProps,
      // ← 回成员页，不回话题列表：私聊只有一个入口，就是名册。手机顶栏那颗 ←
      // 读的是这里，桌面上私聊头里那颗读的是 DmView，两颗指同一个地方。
      meta: { titleKey: 'navigation.pages.dm', hideTabs: true, backTo: 'project-members' },
    },
    {
      // 资料库里的一份文档，整页打开。和资料库是同一个组件：它决定画列表还是这一份。
      name: 'project-document',
      path: `docs/:docId${NUMBER_OR_ID}`,
      component: () => import('@/views/ProjectLibraryView.vue'),
      props: addressProps,
      meta: { titleKey: 'navigation.project.library', hideTabs: true, backTo: 'project-library' },
    },
    {
      name: 'project-docs',
      path: 'docs/:kind',
      component: () => import('@/views/ProjectDocsView.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.docs',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.docs', icon: 'mdi-file-document-outline', params: { kind: 'charter' } },
      },
    },
    {
      // 资料库：用户给这个项目的文件。项目级，所以它在项目这个框里，不在某个话题
      // 下面——引用它的那条消息可能来自任何一个房间。
      name: 'project-library',
      path: 'library',
      component: () => import('@/views/ProjectLibraryView.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.library',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.library', icon: 'mdi-folder-outline' },
      },
    },
    {
      // 浏览频道：项目里我能看到的频道，找、加入、新建（新建侧栏那颗 ＋ 也能开）。
      // 侧栏只列我加入的频道。
      name: 'project-channels',
      path: 'channels',
      component: () => import('@/views/workspace/ChannelBrowse.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.channels',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.channels', icon: 'mdi-pound' },
      },
    },
    {
      // 搜索结果页：命令面板里内容只列前几条，「查看全部结果」进这里看全。词在地址上。
      name: 'project-search',
      path: 'search',
      component: () => import('@/views/ProjectSearchView.vue'),
      props: addressProps,
      meta: { titleKey: 'navigation.search.title', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      // 定时与触发：房间里的 AI 队友按时间或按项目事件自己开工的那些规则。项目级，
      // 因为一条规则的结果可能要看别的房间，而人要在一处看全这个项目有哪些在自己跑。
      name: 'project-routines',
      path: 'routines',
      component: () => import('@/views/ProjectRoutinesView.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.routines',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.routines', icon: 'mdi-timer-cog-outline' },
      },
    },
    {
      // 技能：这个项目存下来的做法。项目级，因为存下来就是给之后每个房间用的。
      name: 'project-skills',
      path: 'skills',
      component: () => import('@/views/ProjectSkillsView.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.skills',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.skills', icon: 'mdi-book-cog-outline' },
      },
    },
    {
      // 清单上的一项产物。项目级，和资料库并列：交付它的那个房间可能已经归档，
      // 而这一项还在，后面每一次交付都算它的新一版。
      name: 'project-artifact',
      path: 'artifacts/:artifactId',
      component: () => import('@/views/ProjectArtifactView.vue'),
      props: addressProps,
      meta: { titleKey: 'navigation.pages.artifact', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      // AI 队友回到了项目设置里：队友的角色设定和模型本来就是这个项目的设置，而
      // 设置页原本只有仓库那几块，对没绑仓库的项目是空的。发出去的旧链接照旧能用。
      path: 'agents',
      redirect: (to) => ({ name: 'project-settings', params: { projectId: to.params.projectId, section: 'agents' } }),
    },
    {
      // 盖在整个窗口上的一层，九栏各有地址（`settings/agents`…）。不带栏时桌面落到第一
      // 栏，手机上是目录。
      name: 'project-settings',
      path: 'settings/:section?',
      component: () => import('@/views/ProjectSettingsView.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.settings',
        hideTabs: true,
        backTo: 'workspace-project',
        settingsOverlay: true,
        palette: { label: 'navigation.project.settings', icon: 'mdi-cog-outline' },
      },
    },
    {
      // 「导出与发布」退役了：它整页只有一块「发布网站」，而发布出去的地址就是这个
      // 项目交出去的东西之一，现在摆在项目总览「做出了什么」的最上面。
      path: 'delivery',
      redirect: (to) => ({ name: 'workspace-overview', params: { projectId: to.params.projectId } }),
    },
    {
      // 名册页和单人主页共用 `members` 这一段路径，父子关系就是它们的关系：
      // /members 是「有谁」，/members/:handle 是「他是谁」。
      name: 'project-members',
      path: 'members',
      component: () => import('@/views/workspace/ProjectMembersView.vue'),
      props: addressProps,
      meta: {
        titleKey: 'navigation.project.members',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.members', icon: 'mdi-account-group-outline' },
      },
    },
    {
      // 同一个个人主页，从名册打开就留在项目这个框里，← 回名册。
      name: 'member',
      path: 'members/:handle',
      component: () => import('@/views/ProfileView.vue'),
      props: addressProps,
      meta: { titleKey: 'navigation.pages.member', hideTabs: true, backTo: 'project-members' },
    },
    ...LEGACY_TOPIC_PATHS,
    ...DOC_KINDS.map(
      (kind): RouteRecordRaw => ({
        path: kind,
        redirect: (to) => ({
          name: 'project-docs',
          params: { projectId: to.params.projectId, kind },
        }),
      })
    ),
  ],
}
