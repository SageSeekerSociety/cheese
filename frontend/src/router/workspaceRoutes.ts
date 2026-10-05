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

// 项目文档 used to be three routes distinguished by NAME rather than by a
// parameter, which is how the same words ended up leading to two different
// places: the sidebar swapped the panel in place, the action card pushed the
// full page. One address per document now; the old links still resolve.
const DOC_KINDS = ['charter', 'weeklies'] as const

// The topic list is the mobile workspace destination. Child pages declare their
// parent through backTo, which is used by both desktop and mobile navigation.
export const workspaceRoutes: RouteRecordRaw = {
  path: '/projects/:projectId',
  components: {
    default: () => import('@/views/workspace/ProjectShell.vue'),
    sidebar: () => import('@/views/workspace/ProjectSidebar.vue'),
  },
  props: { default: true, sidebar: true },
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
      props: true,
      // 手机上这一层就是话题列表，项目名 + 切换器由它自己填进顶栏（barSlot）。
      // 它同时是底栏「工作区」那一格的落点，所以底栏留着，也没有"回上一层"。
      meta: { barSlot: true },
    },
    {
      name: 'workspace-topic',
      path: 'topics/:topicId',
      component: () => import('@/views/workspace/TopicView.vue'),
      props: true,
      // 手机上这是页面栈的末端：底栏收起（它不是一级目的地），← 回到话题列表。
      // `barSlot`: TopicHeader（标题 + #id + 阶段）填的就是顶栏那一格，不再自己
      // 画一条横条；← 由顶栏按 backTo 出。
      meta: { hideTabs: true, backTo: 'workspace-project', barSlot: true },
    },
    {
      // 一个频道的全部任务：侧栏只挂和我有关的几条，其余在这一页。
      name: 'workspace-channel-tasks',
      path: 'topics/:topicId/tasks',
      component: () => import('@/views/workspace/ChannelTasks.vue'),
      props: true,
      meta: { hideTabs: true, backTo: 'workspace-project' },
    },
    {
      // 任务页：一个任务自己的对话和实况文档。和房间页是同一个组件——任务挂在房间下，
      // 房间要先打开，任务页借它的名册和外框；地址里多出来的 taskId 决定画哪一边。
      name: 'workspace-task',
      path: 'topics/:topicId/tasks/:taskId',
      component: () => import('@/views/workspace/TopicView.vue'),
      props: true,
      meta: { hideTabs: true, backTo: 'workspace-project', barSlot: true },
    },
    {
      // 看板: 跨房间的一块板，按「该谁动」分列。房间总览答的是「这个房间在干什么」，
      // 而一个项目有上百个房间——「现在整个项目有什么在跑、有什么在等我」得一个个
      // 点进去才知道，于是没人知道。桌面上侧栏常驻，手机上它是页面栈的一层，← 回
      // 话题列表。
      //
      // 路由名和路径还是 running：改地址会把所有已经发出去的链接打断，而这一页答的
      // 仍然是同一个问题——名字换了，位置没换。
      name: 'workspace-running',
      path: 'running',
      component: () => import('@/views/workspace/RunningWorkView.vue'),
      props: true,
      meta: {
        titleKey: 'navigation.project.board',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.board', icon: 'mdi-view-column-outline' },
      },
    },
    {
      name: 'workspace-dm',
      path: 'dm/:peer',
      component: () => import('@/views/workspace/DmView.vue'),
      props: true,
      // ← 回成员页，不回话题列表：私聊只有一个入口，就是名册。手机顶栏那颗 ←
      // 读的是这里，桌面上私聊头里那颗读的是 DmView，两颗指同一个地方。
      meta: { titleKey: 'navigation.pages.dm', hideTabs: true, backTo: 'project-members' },
    },
    {
      name: 'project-docs',
      path: 'docs/:kind',
      component: () => import('@/views/ProjectDocsView.vue'),
      props: true,
      meta: {
        titleKey: 'navigation.project.docs',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.docs', icon: 'mdi-file-document-outline', params: { kind: 'charter' } },
      },
    },
    {
      // 总览退役了：它答的每一个问题都有一处答得更准的地方——谁在等你、交出去了
      // 什么、对外的地址，都在项目首页上；成员在名册页；额度在项目设置里。发出去
      // 的旧链接落到首页。
      path: 'overview',
      redirect: (to) => ({ name: 'workspace-running', params: { projectId: to.params.projectId } }),
    },
    {
      // 资料库：用户给这个项目的文件。项目级，所以它在项目这个框里，不在某个话题
      // 下面——引用它的那条消息可能来自任何一个房间。
      name: 'project-library',
      path: 'library',
      component: () => import('@/views/ProjectLibraryView.vue'),
      props: true,
      meta: {
        titleKey: 'navigation.project.library',
        hideTabs: true,
        backTo: 'workspace-project',
        palette: { label: 'navigation.project.library', icon: 'mdi-folder-outline' },
      },
    },
    {
      // 搜索结果页：命令面板里内容只列前几条，「查看全部结果」进这里看全。词在地址上。
      name: 'project-search',
      path: 'search',
      component: () => import('@/views/ProjectSearchView.vue'),
      props: true,
      meta: { titleKey: 'navigation.search.title', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      // 定时与触发：房间里的 AI 队友按时间或按项目事件自己开工的那些规则。项目级，
      // 因为一条规则的结果可能要看别的房间，而人要在一处看全这个项目有哪些在自己跑。
      name: 'project-routines',
      path: 'routines',
      component: () => import('@/views/ProjectRoutinesView.vue'),
      props: true,
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
      props: true,
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
      props: true,
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
      props: true,
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
      // 项目交出去的东西之一，现在摆在首页的清单旁边。
      path: 'delivery',
      redirect: (to) => ({ name: 'workspace-running', params: { projectId: to.params.projectId } }),
    },
    {
      // 名册页和单人主页共用 `members` 这一段路径，父子关系就是它们的关系：
      // /members 是「有谁」，/members/:handle 是「他是谁」。
      name: 'project-members',
      path: 'members',
      component: () => import('@/views/workspace/ProjectMembersView.vue'),
      props: true,
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
      props: true,
      meta: { titleKey: 'navigation.pages.member', hideTabs: true, backTo: 'project-members' },
    },
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
