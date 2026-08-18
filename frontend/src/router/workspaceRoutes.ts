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

// 手机上这整棵子树是一条页面栈：话题列表是唯一的一级目的地（底栏「工作区」那一格
// 的落点），其余每一层都收起底栏并说明 ← 回哪儿去。桌面上这些 meta 全都不生效——
// 那儿话题列表是常驻侧栏，没有"上一层"可回。
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
      // 手机上这一层就是话题列表，它自带头（项目名 + 切换器），所以系统顶栏不渲染。
      // 它同时是底栏「工作区」那一格的落点，所以底栏留着，也没有"回上一层"。
      meta: { ownHeader: true },
    },
    {
      name: 'workspace-topic',
      path: 'topics/:topicId',
      component: () => import('@/views/workspace/TopicView.vue'),
      props: true,
      // 手机上这是页面栈的末端：底栏收起（它不是一级目的地），← 回到话题列表。
      // `ownHeader`: 这一页自带 TopicHeader（标题 + #id + 阶段 + ⋯），系统顶栏再
      // 写一遍标题就是两条几乎一样的横条，占掉一屏的 13%——所以那条不渲染，←
      // 长在 TopicHeader 上。桌面上三者都不生效。
      meta: { hideTabs: true, backTo: 'workspace-project', ownHeader: true },
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
      name: 'project-settings',
      path: 'settings',
      component: () => import('@/views/ProjectSettingsView.vue'),
      props: true,
      meta: { title: '项目设置', hideTabs: true, backTo: 'workspace-project' },
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
