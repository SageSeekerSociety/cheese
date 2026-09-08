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
const DOC_KINDS = ['charter', 'decisions', 'weeklies'] as const

// The topic list is the mobile workspace destination. Child pages declare their
// parent through backTo, which is used by both desktop and mobile navigation.
export const workspaceRoutes: RouteRecordRaw = {
  path: '/projects/:projectId',
  components: {
    default: () => import('@/views/workspace/ProjectShell.vue'),
    sidebar: () => import('@/views/workspace/ProjectSidebar.vue'),
  },
  props: { default: true, sidebar: true },
  meta: { title: '项目工作台', isFullPage: true },
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
      meta: { title: '看板', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      name: 'workspace-dm',
      path: 'dm/:peer',
      component: () => import('@/views/workspace/DmView.vue'),
      props: true,
      meta: { title: '私聊', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      name: 'project-docs',
      path: 'docs/:kind',
      component: () => import('@/views/ProjectDocsView.vue'),
      props: true,
      meta: { title: '项目文档', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      name: 'overview',
      path: 'overview',
      component: () => import('@/views/OverviewView.vue'),
      props: true,
      meta: { title: '总览', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      name: 'calendar',
      path: 'calendar',
      component: () => import('@/views/CalendarView.vue'),
      props: true,
      meta: { title: '日历', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      name: 'project-agents',
      path: 'agents',
      component: () => import('@/views/ProjectAgentsView.vue'),
      props: true,
      meta: { title: 'AI 队友', backTo: 'workspace-project' },
    },
    {
      name: 'project-settings',
      path: 'settings',
      component: () => import('@/views/ProjectSettingsView.vue'),
      props: true,
      meta: { title: '项目设置', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      // 名册页和单人主页共用 `members` 这一段路径，父子关系就是它们的关系：
      // /members 是「有谁」，/members/:handle 是「他是谁」。
      name: 'project-members',
      path: 'members',
      component: () => import('@/views/workspace/ProjectMembersView.vue'),
      props: true,
      meta: { title: '成员', hideTabs: true, backTo: 'workspace-project' },
    },
    {
      name: 'member',
      path: 'members/:handle',
      component: () => import('@/views/MemberView.vue'),
      props: true,
      meta: { title: '成员', hideTabs: true, backTo: 'workspace-project' },
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
