/**
 * 预览站里「账号设置与项目工作台那一组整页」：账号设置里的六屏（通用、通知、个人资料、
 * 实名信息、密码与安全、这台设备），和项目工作台的三屏（浏览频道、全部任务、项目总览）。
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
    about:
      '账号设置里的「通用」：这台电脑上的桌面 app 自己的设置，只在 app 里出现。页头先说清这些设置只对这台电脑生效，底下就一行 —— 开机时启动。',
    file: 'src/views/user/settings/General.vue',
    component: General,
    needs: UI_T,
    states: [
      {
        name: '开机时启动',
        note: '唯一那一行：左边写着「开机时启动」和一句说明，右边一颗开关（页面打开时先问过系统，问到之前它是按住的）。',
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
    about:
      '账号设置里的「通知」：三块自上而下 —— 渠道（站内 / 推送 / 邮件）、免打扰（安静时段 / 摘要频率），和八类事件 × 三个渠道的矩阵。拨一下就存，存的结果摆在页头右上角。',
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
        name: '通知偏好读回来了',
        note: '三块都在：两张卡片并排，底下是那张八行三列的矩阵，每一格是一颗圆点。',
        props: {},
        // 矩阵那张表只有读到偏好才画。
        expectSelector: '.notif-matrix',
        expect: '按类型',
      },
      {
        name: '首次加载',
        note: '还没读到偏好：一张卡片的位置上转着圈，别的先不画（免得把「还没读到」画成「都是关的」）。',
        props: { loading: true, prefs: null },
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '读不出来',
        note: '读失败时把那句真实原因就地写出来，并给一颗重试 —— 不是画成一片「都关着」。',
        props: { failed: true, prefs: null, failingReason: '通知设置暂时读不出来' },
        expect: '通知设置暂时读不出来',
        expectSelector: '[role="alert"]',
      },
      {
        name: '正在存矩阵里的一格',
        note: '拨一格矩阵后只那一格变忙：它在原地按住，别处的显示一律不动 —— 整页一起变灰会让人以为「整页都在存」。',
        props: { pending: 'mention.inApp' },
        // 只有正在存的那一格按钮被按住，别的圆点都还亮着。
        expectSelector: 'button.notif-dot[disabled]',
      },
      {
        name: '正在存一处渠道开关',
        note: '拨「站内」那颗开关后同样只那一处变忙：开关自己按住并转圈，别处不动。',
        props: { pending: 'inAppEnabled' },
        // 按住的是「站内」那一颗：它外面那层 v-input 带上了 disabled。
        expectSelector: '.v-input--disabled #notif-in-app',
      },
    ],
  },
  {
    id: 'profile-view',
    title: 'ProfileView',
    about:
      '账号设置里的「个人资料」：头像、昵称、用户名、简介四行，和一个只有改动过才浮出来的保存条。昵称与简介是这一页自己的草稿，改脏了才交上去。',
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
        name: '资料在手',
        note: '默认这一格：页头右边一颗回自己主页的链接，底下四行 —— 头像、昵称、用户名、简介。',
        props: {},
        // 昵称那一栏是这一页自己的草稿框。
        expectSelector: '#profile-nickname',
        expect: '昵称',
      },
      {
        name: '还没读到人',
        note: '容器还没拿到账号时整页只剩一条页头 —— 底下那张表单和回主页的链接都要等人到齐才画。',
        props: { user: null },
        // 页头此时是这一页唯一的子元素（表单那一片整块没画）。
        expectSelector: '.settings-page > header.profile__head:first-child:last-child',
      },
      {
        name: '保存中',
        note: '交上去之后保存条浮出来，右边那颗回执写着「保存中…」。',
        props: { saving: true },
        expectSelector: '.save-status--saving',
      },
      {
        name: '已保存',
        note: '存好了：回执换成绿色的「已保存」，几秒后自己淡出。',
        props: { saved: true },
        expect: '已保存',
        expectSelector: '.save-status--saved',
      },
      {
        name: '存不上去',
        note: '服务端拒了就照写原因，回执是红色的一行（有原因就接在「保存失败」后面）。',
        props: { error: '昵称已被占用' },
        expect: '昵称已被占用',
        expectSelector: '.save-status--error',
      },
    ],
  },
  {
    id: 'real-name-view',
    title: 'RealNameView',
    about:
      '账号设置里的「实名信息」：填过就只显示掩码后的姓名与学号（要看全的另一步确认），没填过给一颗填写的入口，底下是谁看过这条记录的访问流水。',
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
        name: '还没读到',
        note: '记录还没回来：留一块定高的卡片占着位子，页面不会在读到的瞬间跳一下。',
        props: {},
        expectSelector: 'section.realname__pending[aria-busy="true"]',
      },
      {
        name: '读不出来',
        note: '记录读不回来时那块卡片里写一句原因（此时它不再算「在读」）。',
        props: { loadFailed: true },
        expect: '未能加载实名信息',
        expectSelector: 'section.realname__pending[aria-busy="false"]',
      },
      {
        name: '还没填过',
        note: '读到「没有这条记录」：说清还没填，并给一颗填写的入口。',
        props: { loaded: true },
        expect: '还没有填写实名信息',
        expectSelector: 'section.realname__empty',
      },
      {
        name: '填第一遍',
        note: '打开表单时姓名与学号是必填、其余可选，底下两颗：取消和保存。',
        props: { loaded: true, editing: true },
        expect: '填写实名信息',
        // 姓名那一栏：表单里第一格，必填。
        expectSelector: '#realname-realName',
      },
      {
        name: '已经填过了',
        note: '有记录时只显示掩码后的一行行值，右上角给「显示完整信息」和「修改」两颗。',
        props: { loaded: true, record: REAL_NAME_RECORD },
        expect: '你的信息',
        expectSelector: '.realname__actions',
      },
      {
        name: '自己看过的',
        note: '填过之后底下多一栏访问流水：这里摆的是本人自己看过的那两条 —— 去看的标记、一行「你查看了完整信息」、在哪块空间、什么时候。（「别人来看过」那几行要上层的作用域，预览站单独挂不出来，见 fixtures 里的说明。）',
        props: { loaded: true, record: REAL_NAME_RECORD, rows: REAL_NAME_LOGS, logTotal: 2 },
        expect: '你查看了完整信息',
        expectSelector: '.realname__log',
      },
    ],
  },
  {
    id: 'security-view',
    title: 'SecurityView',
    about:
      '账号设置里的「密码与安全」：按「怎么进来」分成三块 —— 登录方式（密码、通行密钥、两步验证）、已关联账号、登录中的浏览器与 app；改密码和开两步验证都在弹窗里。',
    file: 'src/views/user/settings/SecurityView.vue',
    component: SecurityView,
    needs: UI_T,
    // 改密码、开两步验证那两只对话框画在 body 上。
    teleport: true,
    args: { ...SECURITY_PROPS, ...SECURITY_DIALOGS },
    states: [
      {
        name: '三块清单都在',
        note: '默认这一格：有一枚通行密钥（它单独占一行明细）、两步验证关着、一个已关联账号、两台登录中的浏览器。',
        props: {},
        // 通行密钥那一行详情只有「有通行密钥」时才画。
        expectSelector: '.srow--sub',
        expect: '通行密钥',
      },
      {
        name: '浏览器不支持通行密钥',
        note: '这台浏览器建不出通行密钥时那一行写明原因，「添加」那颗被按住。',
        props: { webAuthnSupported: false },
        expect: '当前浏览器不支持通行密钥',
        // 不支持时「添加」那颗画成按下不动的样子。
        expectSelector: '.settings-card button.v-btn--disabled',
      },
      {
        name: '两步验证已开',
        note: '开着两步验证时状态那一行前面多一颗亮着的点，底下也多一栏「备用码」。',
        props: { totpEnabled: true, backupCodes: ['3f2a-11cd-90b8'] },
        expect: '已开启，使用验证器应用',
        expectSelector: '.status-dot--on',
      },
      {
        name: '没有关联账号',
        note: '已关联账号那一块读到的是空：就地写一句「暂无关联账号」，不画成一行空行。',
        props: { connections: [] },
        expect: '暂无关联账号',
        // 第二张卡片（已关联账号）里的空态那一行。
        expectSelector: 'section.settings-card:nth-of-type(2) .srow--empty',
      },
      {
        name: '没有其他登录',
        note: '登录清单读到的是空：那一块就地写一句「暂无其他登录」，右上角那颗「退出其他设备」也不出现（没有别的设备可退）。',
        props: { sessions: [] },
        expect: '暂无其他登录',
        // 第三张卡片（登录中的浏览器与 app）里的空态那一行。
        expectSelector: 'section.settings-card:nth-of-type(3) .srow--empty',
      },
      {
        name: '改密码的弹窗',
        note: '点「修改」弹出来的那一只：新密码和确认密码两栏，两遍不一致会就地写在框上。',
        props: { showChangePassword: true },
        expectSelector: '#security-new-password',
      },
      {
        name: '开两步验证：扫码',
        note: '三步走的第一步：一句「用验证器应用扫描二维码」、那张二维码，以及扫不了时手输的那串密钥。',
        props: { showTotp: true, setupStep: 'qr' },
        expect: 'JBSWY3DPEHPK3PXP',
        expectSelector: '.setup__qr',
      },
      {
        name: '开两步验证：输码',
        note: '第二步：把验证器里那 6 位输进一排格子里，填满才让验证。',
        props: { showTotp: true, setupStep: 'verify' },
        expect: '输入应用中显示的 6 位验证码',
        expectSelector: '.setup .v-otp-input',
      },
      {
        name: '开两步验证：备用码',
        note: '第三步：把这一组备用码列出来，提醒只显示这一次、每个只能用一次，并给一颗整组复制的按钮。',
        props: { showTotp: true, setupStep: 'backup', backupCodes: ['3f2a-11cd-90b8', '77bb-20ef-3311'] },
        expect: '3f2a-11cd-90b8',
        expectSelector: '.setup__codes',
      },
    ],
  },
  {
    id: 'this-device-view',
    title: 'ThisDeviceView',
    about:
      '账号设置里的「这台设备」：桌面 app 所在的这台电脑接入了没有、叫什么、提供给哪些团队、机主自己的 Claude Code，以及把设备断开。',
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
        name: '接入状态还在问',
        note: '还没问到这台设备接入了没有：一行转着圈的进度环，别的先不画。',
        props: { loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '还没接入',
        note: '没接入时给一块空卡片：一句「未接入」和一行说明（接入后 AI 队友能在这台设备上跑任务），加一颗接入的入口。',
        props: { connected: false },
        expect: '未接入',
        expectSelector: '.this-device__empty',
      },
      {
        name: '已接入，在线',
        note: '接入且在线：状态那一行前面一颗亮着的点写着「已接入 · 在线」，底下是设备名、团队、Claude Code 和断开。',
        props: {},
        expect: '已接入 · 在线',
        expectSelector: '.settings-card .this-device__status--on',
      },
      {
        name: '已接入，离线',
        note: '同一台设备掉线了：只有状态那一点和那句话变，其余各行照旧。',
        props: { online: false },
        expect: '已接入 · 离线',
        // 这一格没有「在线」那一颗亮点的形状。
        expectSelector: '.settings-card .this-device__status:not(.this-device__status--on)',
      },
      {
        name: '没提供给团队',
        note: '一台团队都没勾时，团队那一行就地写「未提供给团队」，不画成一片空白。',
        props: { teamIds: [] },
        expect: '未提供给团队',
        expectSelector: '.this-device__chips .this-device__muted',
      },
      {
        name: '带出错了',
        note: '接入状态读失败之类的原因写在顶上一整条，下面的区块照常画。',
        props: { error: '这台设备的接入状态没读出来' },
        expect: '这台设备的接入状态没读出来',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'channel-browse-view',
    title: 'ChannelBrowseView',
    about:
      '项目里的「浏览频道」：我能看到的频道排成一列（能找、能加入、能退出），项目管理者还能在这一页新建频道。侧栏只列我加入的，别的频道从这里找。',
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
        name: '列出频道',
        note: '默认这一格：搜索框、一排筛选（全部 / 已加入 / 已归档，各带个数），底下每个频道一行 —— 名字、说明、人数，右边「加入」或「退出」。',
        props: {},
        expectSelector: '[data-testid="channel-browse-row"]',
        expect: '实验课',
      },
      {
        name: '首次加载',
        note: '一个频道都还没读到、又在读：转着圈，不先说「暂无频道」。',
        props: { loading: true, channels: [] },
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '读不出来',
        note: '频道列表读失败时把那句原因就地写出来并给一颗重试。',
        props: { error: '网络不通，请稍后再试', channels: [] },
        expect: '网络不通，请稍后再试',
        expectSelector: '[role="alert"]',
      },
      {
        name: '一个都看不到',
        note: '读到了、但没有我能看到的频道：就地说一句「暂无频道」。',
        props: { channels: [] },
        expect: '暂无频道',
        expectSelector: 'p.t-body.c-muted',
      },
      {
        name: '新建频道',
        note: '管理者点「新建」弹出来的那一只：名字、说明、一个「仅成员可见」的勾，名字填了才让创建。',
        props: { creatingOpen: true },
        expectSelector: '[data-testid="new-channel"]',
      },
    ],
  },
  {
    id: 'project-tasks-view',
    title: 'ProjectTasksView',
    about:
      '项目里的「全部任务」：任务按「下一步在谁手上」分组（待处理、检查中、进行中、未开始），等人的那几件用暖色写出来；上头能按「谁的」「哪个频道」「看已关闭的」筛。',
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
        name: '进行中的任务',
        note: '默认这一格：一排筛选（全部 / 我负责的 / 我协作的 / 其他人的，各带数）、频道菜单、看已关闭的；底下按状态分组，每行一件任务。',
        props: {},
        expectSelector: '.tasks__row',
        expect: '整理第二章的课件',
      },
      {
        name: '首次加载',
        note: '任务还没读到、又在读：转着圈；筛选项上的数这时先不写（那时的 0 不是真的 0）。',
        props: { loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        name: '读不出来',
        note: '任务列表读失败时把那句话写出来并给一颗重试。',
        props: { failed: true },
        expect: '无法加载任务',
        expectSelector: '[role="alert"]',
      },
      {
        name: '一件都没有',
        note: '读到了、这个筛选下一件都没有：就地说一句「暂无任务」。',
        props: { tasks: [] },
        expect: '暂无任务',
        expectSelector: '.tasks__empty',
      },
      {
        name: '看已关闭的',
        note: '切到「已关闭」时不再分组，全部落进「已完成」那一格，数从服务端给的那一份取。',
        props: { closed: true, closedTasks: [CLOSED_TASK], closedCounts: { all: 1, mine: 1, helping: 0, others: 0 } },
        expectSelector: '.tasks__group',
        expect: '把实验指导书发下去',
      },
      {
        name: '收着几件停滞的',
        note: '十四天没动静的活收进最底下一栏「已停滞」，先只写一件「已停滞 N 件」，点开才列出来。',
        props: { tasks: [...PROJECT_TASKS, STALLED_TASK] },
        expect: '已停滞',
        expectSelector: '[data-testid="tasks-stalled"]',
      },
      {
        name: '只看某个频道',
        note: '从频道菜单选一个频道之后，那一条自己亮起来，列表只剩这个频道里的任务。',
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
    about:
      '进项目落在的这一页：项目总览那份文档、最近进展（按天排）、谁在做什么（按负责人分）、做出了什么（交出去的产物和网站）。',
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
        name: '四块都在',
        note: '默认这一格：左栏是项目总览文档（带一颗「编辑」）和最近进展，右栏是谁在做什么和做出了什么（那两项产物）。',
        props: {},
        // 有文档时才画那颗「编辑」。
        expectSelector: '[data-testid="overview-edit"]',
        expect: '项目总览',
      },
      {
        name: '还没写文档',
        note: '文档是空的（不是没读到）时给一句「暂无项目总览」和一颗「写一份」。',
        props: { overviewText: '' },
        expect: '暂无项目总览',
        expectSelector: '[data-testid="overview-write"]',
      },
      {
        name: '进展读不出来',
        note: '最近进展读失败时只在那一块写清原因并给一颗重试，不把整页变成一条错误。',
        props: { progressFailed: true },
        expect: '无法读取最近进展',
        expectSelector: '[role="alert"]',
      },
      {
        name: '最近两周没有进展',
        note: '读到了、但最近两周没有可说的：就地说一句「最近两周暂无进展」。',
        props: { progress: [] },
        expect: '最近两周暂无进展',
        expectSelector: '[data-testid="overview-progress"] p.ov-empty',
      },
      {
        name: '谁在做什么都空了',
        note: '一件进行中的任务都没有时，那一块写一句「暂无进行中的任务」。',
        props: { tasks: [] },
        expect: '暂无进行中的任务',
        expectSelector: '[data-testid="overview-people"] p.ov-empty',
      },
      {
        name: '领题来的项目带截止',
        note: '从一道题领出来的项目会在文档上面多一行截止：自己的报名批准过就写「提交截止」，否则写「题目截止」。',
        props: { challengeDeadline: { at: Date.UTC(2026, 11, 31, 15, 59, 0), mine: true } },
        expect: '提交截止',
        expectSelector: '[data-testid="overview-deadline"]',
      },
    ],
  },
]
