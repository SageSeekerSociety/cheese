/**
 * 预览站里「账号设置与项目工作台那一组整页」的一册分卷——项目工作台那一组（全部频道、全部任务、项目总览）。
 *
 * 它和同级的另外两份分卷一起，由 `catalogViewSettingsWorkspace.ts` 合并成
 * `VIEW_SETTINGS_WORKSPACE_ENTRIES` 交给 `catalogViews.ts`；各屏公用数据见
 * `catalogViewSettingsWorkspaceFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  artifactApi,
  CHANNEL_ENTRIES,
  CLOSED_TASK,
  OVERVIEW_TEXT,
  PROJECT_PROGRESS,
  PROJECT_TASKS,
  STALLED_TASK,
  TASK_CHANNELS,
  TASK_NAMES,
} from './catalogViewSettingsWorkspaceFixtures'

import { t } from '@/i18n'
import ChannelBrowseView from '@/views/workspace/ChannelBrowseView.vue'
import ProjectOverviewView from '@/views/workspace/ProjectOverviewView.vue'
import ProjectTasksView from '@/views/workspace/ProjectTasksView.vue'

/** 上面那两样，再加一条 name 指得上的路由（页面里的链接、按钮按名字指路时用）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_SETTINGS_WORKSPACE_ENTRIES_C: CatalogEntry[] = [
  {
    id: 'channel-browse-view',
    title: 'ChannelBrowseView',
    get about() {
      return t('catalog.channelBrowseView.about')
    },
    file: 'src/views/workspace/ChannelBrowseView.vue',
    component: ChannelBrowseView,
    needs: UI_T_R,
    // 「新建频道」那只对话框画在 body 上。
    teleport: true,
    args: {
      channels: CHANNEL_ENTRIES,
      loading: false,
      error: null,
      busyId: null,
      creating: false,
      canCreate: true,
      creatingOpen: false,
    },
    states: [
      {
        get name() {
          return t('catalog.channelBrowseView.s1.name')
        },
        get note() {
          return t('catalog.channelBrowseView.s1.note')
        },
        props: {},
        expectSelector: '[data-testid="channel-browse-row"]',
        expect: '实验课',
      },
      {
        get name() {
          return t('catalog.channelBrowseView.s2.name')
        },
        get note() {
          return t('catalog.channelBrowseView.s2.note')
        },
        props: { loading: true, channels: [] },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.channelBrowseView.s3.name')
        },
        get note() {
          return t('catalog.channelBrowseView.s3.note')
        },
        props: { error: '网络不通，请稍后再试', channels: [] },
        expect: '网络不通，请稍后再试',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.channelBrowseView.s4.name')
        },
        get note() {
          return t('catalog.channelBrowseView.s4.note')
        },
        props: { channels: [] },
        expect: '暂无频道',
        expectSelector: 'p.t-body.c-muted',
      },
      {
        get name() {
          return t('catalog.channelBrowseView.s5.name')
        },
        get note() {
          return t('catalog.channelBrowseView.s5.note')
        },
        props: { creatingOpen: true },
        expectSelector: '[data-testid="new-channel"]',
      },
    ],
  },
  {
    id: 'project-tasks-view',
    title: 'ProjectTasksView',
    get about() {
      return t('catalog.projectTasksView.about')
    },
    file: 'src/views/workspace/ProjectTasksView.vue',
    component: ProjectTasksView,
    needs: UI_T_R,
    args: {
      tasks: PROJECT_TASKS,
      channels: TASK_CHANNELS,
      channelId: null,
      names: TASK_NAMES,
      avatars: {},
      me: 'wangxiaoming',
      loading: false,
      failed: false,
      closedTasks: [],
      closedCounts: null,
      closedHasMore: false,
      closedLoading: false,
      closedFailed: false,
      status: 'open',
      delivery: 'all',
      whose: 'all',
    },
    states: [
      {
        get name() {
          return t('catalog.projectTasksView.s1.name')
        },
        get note() {
          return t('catalog.projectTasksView.s1.note')
        },
        props: {},
        expectSelector: '.tasks__row',
        expect: '整理第二章的课件',
      },
      {
        get name() {
          return t('catalog.projectTasksView.s2.name')
        },
        get note() {
          return t('catalog.projectTasksView.s2.note')
        },
        props: { loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.projectTasksView.s3.name')
        },
        get note() {
          return t('catalog.projectTasksView.s3.note')
        },
        props: { failed: true },
        expect: '无法加载任务',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.projectTasksView.s4.name')
        },
        get note() {
          return t('catalog.projectTasksView.s4.note')
        },
        props: { tasks: [] },
        expect: '暂无任务',
        expectSelector: '.tasks__empty',
      },
      {
        get name() {
          return t('catalog.projectTasksView.s5.name')
        },
        get note() {
          return t('catalog.projectTasksView.s5.note')
        },
        props: {
          status: 'closed',
          closedTasks: [CLOSED_TASK],
          closedCounts: { all: 1, mine: 1, helping: 0, others: 0 },
        },
        expectSelector: '.tasks__group',
        expect: '把实验指导书发下去',
      },
      {
        get name() {
          return t('catalog.projectTasksView.s6.name')
        },
        get note() {
          return t('catalog.projectTasksView.s6.note')
        },
        props: { tasks: [...PROJECT_TASKS, STALLED_TASK] },
        expect: '已停滞',
        expectSelector: '[data-testid="tasks-stalled"]',
      },
      {
        get name() {
          return t('catalog.projectTasksView.s7.name')
        },
        get note() {
          return t('catalog.projectTasksView.s7.note')
        },
        props: { channelId: 'ch-lab' },
        expect: '实验课',
        // The selected channel remains independent of lifecycle and delivery.
        expectSelector: '[data-testid="tasks-channel"].tasks__chip--on',
      },
      {
        get name() {
          return t('taskList.allStatuses')
        },
        get note() {
          return t('taskList.catalogAllNote')
        },
        props: {
          status: 'all',
          closedTasks: [CLOSED_TASK],
          closedCounts: { all: 1, mine: 1, helping: 0, others: 0 },
        },
        expect: '交付已采纳',
        expectSelector: '.tasks__accepted',
      },
      {
        get name() {
          return t('taskList.acceptedDelivery')
        },
        get note() {
          return t('taskList.catalogAcceptedNote')
        },
        props: {
          delivery: 'accepted',
          tasks: PROJECT_TASKS.map((task, index) =>
            index === 1 ? { ...task, accepted_at: CLOSED_TASK.accepted_at } : task
          ),
        },
        expect: '整理第二章的课件',
        expectSelector: '.tasks__row',
      },
    ],
  },
  {
    id: 'project-overview-view',
    title: 'ProjectOverviewView',
    get about() {
      return t('catalog.projectOverviewView.about')
    },
    file: 'src/views/workspace/ProjectOverviewView.vue',
    component: ProjectOverviewView,
    needs: UI_T_R,
    args: {
      projectId: 'p-course',
      projectName: '操作系统 2026 秋',
      overviewText: OVERVIEW_TEXT,
      challengeDeadline: null,
      progress: PROJECT_PROGRESS,
      progressFailed: false,
      tasks: PROJECT_TASKS,
      // 「进行中的任务」那个数（清单第 14 条）和下面这几件对得上：四件都还没做完。
      underway: 4,
      names: TASK_NAMES,
      avatars: {},
      me: 'wangxiaoming',
      artifactApi: artifactApi(),
    },
    states: [
      {
        get name() {
          return t('catalog.projectOverviewView.s1.name')
        },
        get note() {
          return t('catalog.projectOverviewView.s1.note')
        },
        props: {},
        // 有文档时才画那颗「编辑」。
        expectSelector: '[data-testid="overview-edit"]',
        expect: '项目目标',
      },
      {
        get name() {
          return t('catalog.projectOverviewView.s2.name')
        },
        get note() {
          return t('catalog.projectOverviewView.s2.note')
        },
        props: { overviewText: '' },
        expect: '暂无项目目标',
        expectSelector: '[data-testid="overview-write"]',
      },
      {
        get name() {
          return t('catalog.projectOverviewView.s3.name')
        },
        get note() {
          return t('catalog.projectOverviewView.s3.note')
        },
        props: { progressFailed: true },
        expect: '无法读取最近进展',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.projectOverviewView.s4.name')
        },
        get note() {
          return t('catalog.projectOverviewView.s4.note')
        },
        props: { progress: [] },
        expect: '最近两周暂无进展',
        expectSelector: '[data-testid="overview-progress"] p.ov-empty',
      },
      {
        get name() {
          return t('catalog.projectOverviewView.s5.name')
        },
        get note() {
          return t('catalog.projectOverviewView.s5.note')
        },
        props: { tasks: [], underway: 0 },
        expect: '暂无进行中的任务',
        expectSelector: '[data-testid="overview-people"] p.ov-empty',
      },
      {
        get name() {
          return t('catalog.projectOverviewView.s6.name')
        },
        get note() {
          return t('catalog.projectOverviewView.s6.note')
        },
        props: { challengeDeadline: { at: Date.UTC(2026, 11, 31, 15, 59, 0), mine: true } },
        expect: '提交截止',
        expectSelector: '[data-testid="overview-deadline"]',
      },
    ],
  },
]
