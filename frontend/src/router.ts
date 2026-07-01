import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import WorkspaceView from './views/WorkspaceView.vue'
import OverviewView from './views/OverviewView.vue'
import CalendarView from './views/CalendarView.vue'
import ProjectDocsView from './views/ProjectDocsView.vue'
import SpacesView from './views/SpacesView.vue'
import SpaceBoardView from './views/SpaceBoardView.vue'
import MemberView from './views/MemberView.vue'
import ProjectSettingsView from './views/ProjectSettingsView.vue'
import MarketView from './views/MarketView.vue'

const routes: RouteRecordRaw[] = [
  // 工作台: sidebar + chat + doc. The optional :projectId pre-selects a project.
  { path: '/', name: 'workspace', component: WorkspaceView },
  {
    path: '/project/:projectId',
    name: 'workspace-project',
    component: WorkspaceView,
    props: true,
  },
  // 项目总览 / 收件箱 (eval G2/G3).
  {
    path: '/project/:projectId/overview',
    name: 'overview',
    component: OverviewView,
    props: true,
  },
  // 日历 / 时间维度 (spec §7.2): per-project deadline timeline.
  {
    path: '/project/:projectId/calendar',
    name: 'calendar',
    component: CalendarView,
    props: true,
  },
  // 项目级文档 (spec §7.1): 章程 / 决策记录 / 周报集 open as full-width pages,
  // not hung under any topic. A single ProjectDocsView switches on route name.
  {
    path: '/project/:projectId/charter',
    name: 'project-charter',
    component: ProjectDocsView,
    props: true,
  },
  {
    path: '/project/:projectId/decisions',
    name: 'project-decisions',
    component: ProjectDocsView,
    props: true,
  },
  {
    path: '/project/:projectId/weeklies',
    name: 'project-weeklies',
    component: ProjectDocsView,
    props: true,
  },
  // 项目设置 (design v3): pick the project's AI + compute resource pools.
  {
    path: '/project/:projectId/settings',
    name: 'project-settings',
    component: ProjectSettingsView,
    props: true,
  },
  // 成员页 / portfolio (spec §7.2).
  {
    path: '/project/:projectId/members/:handle',
    name: 'member',
    component: MemberView,
    props: true,
  },
  // 市场 (design v3): browse the AI + compute resource-pool catalog.
  { path: '/market', name: 'market', component: MarketView },
  // 机构看板 (eval F3): a spaces picker + per-space board.
  { path: '/spaces', name: 'spaces', component: SpacesView },
  {
    path: '/space/:spaceId',
    name: 'space-board',
    component: SpaceBoardView,
    props: true,
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
