/**
 * 预览站里「账号设置与项目工作台那一组整页」：账号设置里的六屏（通用、通知、个人资料、
 * 实名信息、密码与安全、这台设备），和项目工作台的三屏（全部频道、全部任务、项目总览）。
 *
 * 这些页面在应用里都由各自的容器取数（`settings/*.vue`、`workspace/*.vue` 那一批），
 * 容器碰接口层，预览站碰不得。好在它们都已经拆成「容器 → 只吃 props 的画面」：这里摆的
 * 正是产品里真会走到的那几格 —— 数据到货 / 首次加载 / 读失败 / 空 / 某一格正忙 / 某一
 * 个弹窗开着。各屏公用的一份数据见 `catalogViewSettingsWorkspaceFixtures.ts`。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的
 * `CatalogEntry`、`CatalogNeed` 是 type-only 引用：`catalog.ts` 反过来要分册的值，
 * 运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  artifactApi,
  CHANNEL_ENTRIES,
  CLOSED_TASK,
  DEVICE_TEAMS,
  NOTIF_PREFS,
  OVERVIEW_TEXT,
  PROJECT_PROGRESS,
  PROJECT_TASKS,
  REAL_NAME_LOGS,
  REAL_NAME_RECORD,
  SECURITY_DIALOGS,
  SECURITY_PROPS,
  SETTINGS_USER,
  STALLED_TASK,
  TASK_CHANNELS,
  TASK_NAMES,
} from './catalogViewSettingsWorkspaceFixtures'

import { t } from '@/i18n'
import General from '@/views/user/settings/General.vue'
import NotificationsView from '@/views/user/settings/NotificationsView.vue'
import ProfileView from '@/views/user/settings/ProfileView.vue'
import RealNameView from '@/views/user/settings/RealNameView.vue'
import SecurityView from '@/views/user/settings/SecurityView.vue'
import ThisDeviceView from '@/views/user/settings/ThisDeviceView.vue'
import ChannelBrowseView from '@/views/workspace/ChannelBrowseView.vue'
import ProjectOverviewView from '@/views/workspace/ProjectOverviewView.vue'
import ProjectTasksView from '@/views/workspace/ProjectTasksView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 上面那两样，再加一条 name 指得上的路由（页面里的链接、按钮按名字指路时用）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_SETTINGS_WORKSPACE_ENTRIES: CatalogEntry[] = [
  {
    id: 'settings-general-view',
    title: 'General',
    get about() {
      return t('catalog.settingsGeneralView.about')
    },
    file: 'src/views/user/settings/General.vue',
    component: General,
    needs: UI_T,
    states: [
      {
        get name() {
          return t('catalog.settingsGeneralView.s1.name')
        },
        get note() {
          return t('catalog.settingsGeneralView.s1.note')
        },
        props: {},
        // 这一行只有这一件控件：那颗开关的输入框是它自己的锚。
        expectSelector: '#general-open-at-login',
        expect: '开机时启动',
      },
    ],
  },
  {
    id: 'notifications-view',
    title: 'NotificationsView',
    get about() {
      return t('catalog.notificationsView.about')
    },
    file: 'src/views/user/settings/NotificationsView.vue',
    component: NotificationsView,
    needs: UI_T,
    args: {
      prefs: NOTIF_PREFS,
      loading: false,
      failed: false,
      failingReason: null,
      saving: false,
      saved: false,
      error: '',
      pending: null,
    },
    states: [
      {
        get name() {
          return t('catalog.notificationsView.s1.name')
        },
        get note() {
          return t('catalog.notificationsView.s1.note')
        },
        props: {},
        // 矩阵那张表只有读到偏好才画。
        expectSelector: '.notif-matrix',
        expect: '按类型',
      },
      {
        get name() {
          return t('catalog.notificationsView.s2.name')
        },
        get note() {
          return t('catalog.notificationsView.s2.note')
        },
        props: { loading: true, prefs: null },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.notificationsView.s3.name')
        },
        get note() {
          return t('catalog.notificationsView.s3.note')
        },
        props: { failed: true, prefs: null, failingReason: '通知设置暂时读不出来' },
        expect: '通知设置暂时读不出来',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.notificationsView.s4.name')
        },
        get note() {
          return t('catalog.notificationsView.s4.note')
        },
        props: { pending: 'mention.inApp' },
        // 只有正在存的那一格按钮被按住，别的圆点都还亮着。
        expectSelector: 'button.notif-dot[disabled]',
      },
      {
        get name() {
          return t('catalog.notificationsView.s5.name')
        },
        get note() {
          return t('catalog.notificationsView.s5.note')
        },
        props: { pending: 'inAppEnabled' },
        // 按住的是「站内」那一颗：它外面那层 v-input 带上了 disabled。
        expectSelector: '.v-input--disabled #notif-in-app',
      },
    ],
  },
  {
    id: 'profile-view',
    title: 'ProfileView',
    get about() {
      return t('catalog.profileView.about')
    },
    file: 'src/views/user/settings/ProfileView.vue',
    component: ProfileView,
    needs: UI_T_R,
    args: {
      user: SETTINGS_USER,
      shownAvatar: '',
      avatarSeed: '王小明',
      canRemoveAvatar: true,
      changingAvatar: false,
      removingAvatar: false,
      avatarAccept: 'image/png,image/jpeg,image/webp,image/gif',
      savedNickname: '王小明',
      savedIntro: '教操作系统，也带嵌入式。',
      saving: false,
      saved: false,
      error: '',
    },
    states: [
      {
        get name() {
          return t('catalog.profileView.s1.name')
        },
        get note() {
          return t('catalog.profileView.s1.note')
        },
        props: {},
        // 昵称那一栏是这一页自己的草稿框。
        expectSelector: '#profile-nickname',
        expect: '昵称',
      },
      {
        get name() {
          return t('catalog.profileView.s2.name')
        },
        get note() {
          return t('catalog.profileView.s2.note')
        },
        props: { user: null },
        // 页头此时是这一页唯一的子元素（表单那一片整块没画）。
        expectSelector: '.settings-page > header.profile__head:first-child:last-child',
      },
      {
        get name() {
          return t('catalog.profileView.s3.name')
        },
        get note() {
          return t('catalog.profileView.s3.note')
        },
        props: { saving: true },
        expectSelector: '.save-status--saving',
      },
      {
        get name() {
          return t('catalog.profileView.s4.name')
        },
        get note() {
          return t('catalog.profileView.s4.note')
        },
        props: { saved: true },
        expect: '已保存',
        expectSelector: '.save-status--saved',
      },
      {
        get name() {
          return t('catalog.profileView.s5.name')
        },
        get note() {
          return t('catalog.profileView.s5.note')
        },
        props: { error: '昵称已被占用' },
        expect: '昵称已被占用',
        expectSelector: '.save-status--error',
      },
    ],
  },
  {
    id: 'real-name-view',
    title: 'RealNameView',
    get about() {
      return t('catalog.realNameView.about')
    },
    file: 'src/views/user/settings/RealNameView.vue',
    component: RealNameView,
    needs: UI_T_R,
    args: {
      loaded: false,
      loadFailed: false,
      editing: false,
      record: null,
      full: null,
      revealing: false,
      opening: false,
      saving: false,
      deleting: false,
      rows: [],
      logTotal: 0,
      logsFailed: false,
      logsHaveMore: false,
      loadingLogs: false,
    },
    states: [
      {
        get name() {
          return t('catalog.realNameView.s1.name')
        },
        get note() {
          return t('catalog.realNameView.s1.note')
        },
        props: {},
        expectSelector: 'section.realname__pending[aria-busy="true"]',
      },
      {
        get name() {
          return t('catalog.realNameView.s2.name')
        },
        get note() {
          return t('catalog.realNameView.s2.note')
        },
        props: { loadFailed: true },
        expect: '未能加载实名信息',
        expectSelector: 'section.realname__pending[aria-busy="false"]',
      },
      {
        get name() {
          return t('catalog.realNameView.s3.name')
        },
        get note() {
          return t('catalog.realNameView.s3.note')
        },
        props: { loaded: true },
        expect: '还没有填写实名信息',
        expectSelector: 'section.realname__empty',
      },
      {
        get name() {
          return t('catalog.realNameView.s4.name')
        },
        get note() {
          return t('catalog.realNameView.s4.note')
        },
        props: { loaded: true, editing: true },
        expect: '填写实名信息',
        // 姓名那一栏：表单里第一格，必填。
        expectSelector: '#realname-realName',
      },
      {
        get name() {
          return t('catalog.realNameView.s5.name')
        },
        get note() {
          return t('catalog.realNameView.s5.note')
        },
        props: { loaded: true, record: REAL_NAME_RECORD },
        expect: '你的信息',
        expectSelector: '.realname__actions',
      },
      {
        get name() {
          return t('catalog.realNameView.s6.name')
        },
        get note() {
          return t('catalog.realNameView.s6.note')
        },
        props: { loaded: true, record: REAL_NAME_RECORD, rows: REAL_NAME_LOGS, logTotal: 2 },
        expect: '你查看了完整信息',
        expectSelector: '.realname__log',
      },
    ],
  },
  {
    id: 'security-view',
    title: 'SecurityView',
    get about() {
      return t('catalog.securityView.about')
    },
    file: 'src/views/user/settings/SecurityView.vue',
    component: SecurityView,
    needs: UI_T,
    // 改密码、开两步验证那两只对话框画在 body 上。
    teleport: true,
    args: { ...SECURITY_PROPS, ...SECURITY_DIALOGS },
    states: [
      {
        get name() {
          return t('catalog.securityView.s1.name')
        },
        get note() {
          return t('catalog.securityView.s1.note')
        },
        props: {},
        // 通行密钥那一行详情只有「有通行密钥」时才画。
        expectSelector: '.srow--sub',
        expect: '通行密钥',
      },
      {
        get name() {
          return t('catalog.securityView.s2.name')
        },
        get note() {
          return t('catalog.securityView.s2.note')
        },
        props: { webAuthnSupported: false },
        expect: '当前浏览器不支持通行密钥',
        // 不支持时「添加」那颗画成按下不动的样子。
        expectSelector: '.settings-card button.v-btn--disabled',
      },
      {
        get name() {
          return t('catalog.securityView.s3.name')
        },
        get note() {
          return t('catalog.securityView.s3.note')
        },
        props: { totpEnabled: true, backupCodes: ['3f2a-11cd-90b8'] },
        expect: '已开启，使用验证器应用',
        expectSelector: '.status-dot--on',
      },
      {
        get name() {
          return t('catalog.securityView.s4.name')
        },
        get note() {
          return t('catalog.securityView.s4.note')
        },
        props: { connections: [] },
        expect: '暂无关联账号',
        // 第二张卡片（已关联账号）里的空态那一行。
        expectSelector: 'section.settings-card:nth-of-type(2) .srow--empty',
      },
      {
        get name() {
          return t('catalog.securityView.s5.name')
        },
        get note() {
          return t('catalog.securityView.s5.note')
        },
        props: { sessions: [] },
        expect: '暂无其他登录',
        // 第三张卡片（登录中的浏览器与 app）里的空态那一行。
        expectSelector: 'section.settings-card:nth-of-type(3) .srow--empty',
      },
      {
        get name() {
          return t('catalog.securityView.s6.name')
        },
        get note() {
          return t('catalog.securityView.s6.note')
        },
        props: { showChangePassword: true },
        expectSelector: '#security-new-password',
      },
      {
        get name() {
          return t('catalog.securityView.s7.name')
        },
        get note() {
          return t('catalog.securityView.s7.note')
        },
        props: { showTotp: true, setupStep: 'qr' },
        expect: 'JBSWY3DPEHPK3PXP',
        expectSelector: '.setup__qr',
      },
      {
        get name() {
          return t('catalog.securityView.s8.name')
        },
        get note() {
          return t('catalog.securityView.s8.note')
        },
        props: { showTotp: true, setupStep: 'verify' },
        expect: '输入应用中显示的 6 位验证码',
        expectSelector: '.setup .v-otp-input',
      },
      {
        get name() {
          return t('catalog.securityView.s9.name')
        },
        get note() {
          return t('catalog.securityView.s9.note')
        },
        props: { showTotp: true, setupStep: 'backup', backupCodes: ['3f2a-11cd-90b8', '77bb-20ef-3311'] },
        expect: '3f2a-11cd-90b8',
        expectSelector: '.setup__codes',
      },
    ],
  },
  {
    id: 'this-device-view',
    title: 'ThisDeviceView',
    get about() {
      return t('catalog.thisDeviceView.about')
    },
    file: 'src/views/user/settings/ThisDeviceView.vue',
    component: ThisDeviceView,
    needs: UI_T,
    // 「断开」那颗的确认框画在 body 上。
    teleport: true,
    args: {
      loading: false,
      connected: true,
      online: true,
      connecting: false,
      name: '工作站',
      teams: DEVICE_TEAMS,
      teamIds: [2],
      claudeLoggedIn: true,
      claudePlan: 'max',
      claudeService: null,
      claudeState: 'idle',
      canUseModelService: true,
      error: null,
    },
    states: [
      {
        get name() {
          return t('catalog.thisDeviceView.s1.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s1.note')
        },
        props: { loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s2.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s2.note')
        },
        props: { connected: false },
        expect: '未接入',
        expectSelector: '.this-device__empty',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s3.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s3.note')
        },
        props: {},
        expect: '已接入 · 在线',
        expectSelector: '.settings-card .this-device__status--on',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s4.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s4.note')
        },
        props: { online: false },
        expect: '已接入 · 离线',
        // 这一格没有「在线」那一颗亮点的形状。
        expectSelector: '.settings-card .this-device__status:not(.this-device__status--on)',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s5.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s5.note')
        },
        props: { teamIds: [] },
        expect: '未提供给团队',
        expectSelector: '.this-device__chips .this-device__muted',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s6.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s6.note')
        },
        props: { error: '这台设备的接入状态没读出来' },
        expect: '这台设备的接入状态没读出来',
        expectSelector: '[role="alert"]',
      },
    ],
  },
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
      closed: false,
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
        props: { closed: true, closedTasks: [CLOSED_TASK], closedCounts: { all: 1, mine: 1, helping: 0, others: 0 } },
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
        // 频道那条自己亮着（「看已关闭的」没亮）。
        expectSelector: '[data-testid="tasks-channel"].tasks__chip--on',
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
        props: { tasks: [] },
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
