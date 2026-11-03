/**
 * 「账号设置与项目工作台那一组整页条目」（`catalogViewSettingsWorkspace.ts`）用的数据。
 *
 * 全按产品里的真形状写：通知偏好照 `@/lib/notificationPreferences` 的
 * `NotificationPreferences`，实名照 `users/types` 的 `RealNameInfo`，频道照
 * `@/types/channelDirectory` 的 `ChannelEntry`，任务照 `@/cx_types` 的 `RoomTask`，
 * 进展照 `@/types/projectProgress` 的 `ProgressItem`。
 *
 * 预览站碰不得接口层，所以容器算好的那几件在这里写死。几样**不从 API 层
 * 取类型**：实名的记录与访问记录、通行密钥、登录中的浏览器、以及 `SecurityView`
 * 那几个 props —— 它们各自承载在页面组件上，从 `$props` 里取（demo 源文件引用旧的
 * 网络层会把 `legacy-network` 的账记上新的一笔）。
 */
import type { AssistantActivityItem, AssistantMemory, AssistantRoutine, AssistantRules, ProjectArtifact } from '@/api'
import type { BoardPhrase, ListPayload, MyTeam, ProjectSite, ProjectSiteInfo, RoomTask } from '@/cx_types'
import type { NotificationPreferences } from '@/lib/notificationPreferences'
import type { UserRefTarget } from '@/lib/userRef'
import type { ChannelEntry } from '@/types/channelDirectory'
import type { AccountConnection, ModelConnection } from '@/types/modelConnections'
import type { ProgressItem } from '@/types/projectProgress'
import type { User } from '@/types/users'
import type RealNameView from '@/views/user/settings/RealNameView.vue'
import type SecurityView from '@/views/user/settings/SecurityView.vue'
import type ProjectOverviewView from '@/views/workspace/ProjectOverviewView.vue'

/** 实名的记录（`RealNameView` 的 `record` / `full`）。 */
export type RealNameInfo = NonNullable<InstanceType<typeof RealNameView>['$props']['record']>
/** 访问记录里的一行（`RealNameView` 的 `rows` 里的一条）。 */
type RealNameLogRow = InstanceType<typeof RealNameView>['$props']['rows'][number]
/** 一枚通行密钥（`SecurityView` 的 `passkeys` 里的一条）。 */
type PasskeyInfo = InstanceType<typeof SecurityView>['$props']['passkeys'][number]
/** 一次登录（`SecurityView` 的 `sessions` 里的一条）。 */
type SessionInfo = InstanceType<typeof SecurityView>['$props']['sessions'][number]
/** 产物清单的读写（`ArtifactManifest` 的 `api`）。 */
export type WorkspaceArtifactApi = InstanceType<typeof ProjectOverviewView>['$props']['artifactApi']

/** 一个固定「现在」，让「几小时前」这类相对时刻每次都落在同一格。 */
const NOW = Date.UTC(2026, 9, 11, 12, 0, 0)
const HOUR = 3_600_000
const DAY = 86_400_000
/** 相对那一刻的 ISO 时刻。 */
const ago = (ms: number) => new Date(NOW - ms).toISOString()

// ---- 人 --------------------------------------------------------------------

/** 屏幕上的那个人：昵称、简介、主页都在。 */
export const SETTINGS_USER: User = {
  id: 1,
  username: 'wangxiaoming',
  nickname: '王小明',
  avatarId: 11,
  intro: '教操作系统，也带嵌入式。',
  question_count: 6,
  answer_count: 24,
}

// ---- 通知偏好（`NotificationsView` 的 `prefs`）-----------------------------

/** 一份读回来的通知偏好：渠道、安静时段、摘要、以及八类事件 × 三渠道的矩阵。 */
export const NOTIF_PREFS: NotificationPreferences = {
  inAppEnabled: true,
  pushEnabled: true,
  emailMode: 'digest',
  quietHoursEnabled: true,
  quietHoursStart: '22:00',
  quietHoursEnd: '08:00',
  digestCadence: 'weekly',
  events: {
    mention: { inApp: true, push: true, email: false },
    reply: { inApp: true, push: false, email: false },
    reaction: { inApp: true, push: false, email: false },
    waitsOnMe: { inApp: true, push: true, email: true },
    deviceInUse: { inApp: true, push: true, email: false },
    invitation: { inApp: true, push: false, email: true },
    announcement: { inApp: true, push: false, email: false },
    billing: { inApp: true, push: false, email: true },
    assistant: { inApp: true, push: true, email: true },
  },
}

// ---- 实名（`RealNameView`）-------------------------------------------------

/** 已经填过的那一份实名：姓名与学号是必填，其余可空。 */
export const REAL_NAME_RECORD: RealNameInfo = {
  realName: '王小明',
  studentId: '2023010101',
  grade: '2023',
  major: '计算机科学与技术',
  className: '计算机 2301',
}

/** 访问记录里的一个人名去处。 */
const userTo = (handle: string): UserRefTarget => ({ name: 'UserPage', params: { handle } })

/** 自己看自己那条实名留下的记录。
 *
 * 这里**只摆自己看的**：「别人来看过 / 导出过」那几行模板里走 `<i18n-t>`，它认的
 * 是上层的作用域（产品里由设置那一层 `layouts/user/Settings.vue` 的 `useI18n` 给），
 * 预览站单独挂这一件时没有那一层，会落一句 `Not found parent scope` 的警告 —— 所以
 * 「谁来查过」那一格收成了「自己看过」：走的是同一条记录行，只是发言的是本人。 */
export const REAL_NAME_LOGS: RealNameLogRow[] = [
  {
    entry: {
      accessor: { ...SETTINGS_USER, id: 1, username: 'wangxiaoming', nickname: '王小明' },
      accessModuleType: 'SPACE',
      accessEntityId: 7,
      accessEntityName: '操作系统 2026 秋',
      accessTime: NOW - 2 * HOUR,
      // 这是一次「查看」：走的枚举值是产品里那一个，照页面 `$props` 推出来的类型写。
      accessType: 'VIEW' as RealNameLogRow['entry']['accessType'],
    },
    name: '王小明',
    avatarUrl: '',
    to: userTo('wangxiaoming'),
    isOwn: true,
    isExport: false,
  },
  {
    entry: {
      accessor: { ...SETTINGS_USER, id: 1, username: 'wangxiaoming', nickname: '王小明' },
      accessModuleType: 'SPACE',
      accessEntityId: 9,
      accessEntityName: '数据结构 2026 秋',
      accessTime: NOW - DAY,
      accessType: 'VIEW' as RealNameLogRow['entry']['accessType'],
    },
    name: '王小明',
    avatarUrl: '',
    to: userTo('wangxiaoming'),
    isOwn: true,
    isExport: false,
  },
]

// ---- 密码与安全（`SecurityView`）-------------------------------------------

/** 一枚同步到多台设备的通行密钥。 */
export const SECURITY_PASSKEYS: PasskeyInfo[] = [
  { id: 'pk-1', createdAt: new Date(NOW - 30 * DAY), deviceType: 'multiDevice', backedUp: true },
]

/** 已关联的第三方账号：学校账号和 GitHub。 */
const SECURITY_CONNECTIONS: InstanceType<typeof SecurityView>['$props']['connections'] = [
  {
    id: 1,
    providerId: 'school-sso',
    providerName: '学校账号',
    providerUserId: '2023010101',
    connectedAt: ago(90 * DAY),
    login: 'wangxiaoming@school.edu',
    tokenExpires: ago(-DAY),
    hasRefreshToken: true,
  },
  {
    id: 2,
    providerId: 'github_app',
    providerName: 'GitHub',
    providerUserId: '583231',
    connectedAt: ago(60 * DAY),
    login: 'wangxiaoming',
    tokenExpires: ago(-DAY),
    hasRefreshToken: true,
  },
]

/** 登录中的浏览器：一台是当前这台，一台是别的设备。 */
const SECURITY_SESSIONS: SessionInfo[] = [
  {
    id: 'sess-1',
    loginMethod: 'password',
    ipAddress: '10.0.0.2',
    userAgent:
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    createdAt: ago(2 * DAY),
    lastActiveAt: ago(HOUR),
    current: true,
    trusted: true,
  },
  {
    id: 'sess-2',
    loginMethod: 'passkey',
    ipAddress: '10.0.0.9',
    userAgent:
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    createdAt: ago(5 * DAY),
    lastActiveAt: ago(6 * HOUR),
    current: false,
    trusted: false,
  },
]

/** 两步验证那一屏的两个登录方式摘要 + 三块清单。 */
export const SECURITY_PROPS = {
  username: 'wangxiaoming',
  closingAccount: false,
  closeError: '',
  webAuthnSupported: true,
  passkeys: SECURITY_PASSKEYS,
  addingPasskey: false,
  deletingPasskey: null as string | null,
  totpEnabled: false,
  totpBusy: false,
  generatingCodes: false,
  connections: SECURITY_CONNECTIONS,
  connectionsLoaded: true,
  unbinding: null as number | null,
  sessions: SECURITY_SESSIONS,
  sessionsLoaded: true,
  signingOut: null as string | null,
  signingOutOthers: false,
  changingPassword: false,
  codesOnly: false,
  totpSecret: 'JBSWY3DPEHPK3PXP',
  qrCodeData: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg"/>',
  verifyError: '',
  backupCodes: [] as string[],
}

/** 弹窗默认全关、停在扫码那一步。 */
export const SECURITY_DIALOGS = {
  showChangePassword: false,
  showTotp: false,
  showCloseAccount: false,
  setupStep: 'qr' as const,
  verificationCode: '',
}

// ---- 这台设备（`ThisDeviceView`）-------------------------------------------

/** 可供选择的团队：一个个人团队、一个真团队。 */
export const DEVICE_TEAMS: MyTeam[] = [
  { id: 1, handle: 'wangxiaoming', name: '王小明', personal: true },
  { id: 2, handle: 'data', name: '数据组' },
]

// ---- 模型连接（`ModelConnectionsView`）-------------------------------------

/** A full platform pool: names as the gateway labels them, a few carrying a trailing note. */
export const PLATFORM_MODELS: { id: string; label: string }[] = [
  'Claude Opus 4.8',
  'Claude Sonnet 4.8',
  'Claude Haiku 4.6',
  'GPT-5.5',
  'GPT-5.5 mini',
  'Gemini 4 Pro',
  'Gemini 4 Argon',
  'Gemini 4 Flash',
  'DeepSeek V4.1',
  'DeepSeek V4.1 Flash',
  'GLM-5.2',
  'GLM-5.2 Air',
  'Kimi K3',
  'Kimi K3 Turbo',
  'MiMo V2.6 Pro',
  'MiMo V2.6 Flash（免费·限时，请求可能用于训练）',
  'Qwen 4 Max',
  'Qwen 4 Coder',
  'Qwen 4 Coder Flash（免费·限时）',
  'MiniMax M3',
  'MiniMax M3 Lightning（免费·限时，请求可能用于训练）',
  'Doubao Seed 2.5 Pro',
  'Doubao Seed 2.5 Lite',
  'Step 4',
  'Hunyuan T2',
  'ERNIE 5.5',
  'Grok 5（免费·限时，请求可能用于训练）',
].map((label) => ({ id: label.toLowerCase().replace(/[^a-z0-9.]+/g, '-'), label }))

/** Team and personal subscriptions, including an offline storage computer. */
export const MODEL_CONNECTIONS: ModelConnection[] = [
  {
    id: 'dev-desk',
    can_manage: true,
    can_remove: false,
    local_id: 'default',
    allowed_models: ['sonnet', 'opus'],
    device_id: 'dev-desk',
    device_name: '工作站',
    online: true,
    installed: true,
    logged_in: true,
    auth_method: 'claude.ai',
    subscription_type: 'max',
    name: null,
    checked_at: ago(HOUR / 4),
    last_used_at: ago(2 * HOUR),
    revoking: false,
  },
  {
    id: 'dev-laptop',
    can_manage: false,
    can_remove: true,
    owner_user_id: 42,
    owner_name: '王小明',
    local_id: 'default',
    allowed_models: ['sonnet', 'opus'],
    device_id: 'dev-laptop',
    device_name: 'credential-host-7c821a',
    online: true,
    installed: true,
    logged_in: true,
    auth_method: 'claude.ai',
    subscription_type: 'pro',
    name: '王小明的 Claude',
    checked_at: ago(HOUR),
    last_used_at: null,
    revoking: false,
  },
  {
    id: 'dev-lab',
    can_manage: true,
    can_remove: false,
    local_id: 'default',
    allowed_models: ['sonnet', 'opus'],
    device_id: 'dev-lab',
    device_name: '实验室服务器',
    online: false,
    installed: true,
    logged_in: true,
    auth_method: 'claude.ai',
    subscription_type: 'max',
    name: null,
    checked_at: ago(3 * DAY),
    last_used_at: ago(3 * DAY),
    revoking: false,
  },
  {
    id: 'login-desk-pro',
    can_manage: true,
    can_remove: false,
    allowed_models: ['sonnet', 'opus', 'haiku'],
    local_id: 'K3V7Q2M8XWZ4TN6PRJ5BHD2C7A',
    device_id: 'dev-desk',
    device_name: '工作站',
    online: true,
    installed: true,
    logged_in: true,
    auth_method: 'claude.ai',
    subscription_type: 'pro',
    name: '实验室的 Pro',
    checked_at: ago(HOUR / 4),
    last_used_at: null,
    revoking: false,
  },
]

/** 保存在账号里的两份：一个起了名字的 API key，一个自定义模型服务。 */
export const MODEL_HELD: AccountConnection[] = [
  {
    id: 'held-key',
    can_manage: true,
    can_remove: false,
    kind: 'api_key',
    allowed_models: ['sonnet', 'opus'],
    name: '工作用',
    key_last4: 'Q7zK',
    service_host: null,
    model: null,
    checked_at: ago(HOUR / 2),
    check_error: null,
    created_at: ago(DAY),
  },
  {
    id: 'held-glm',
    can_manage: true,
    can_remove: false,
    kind: 'model_service',
    allowed_models: ['kimi-k2'],
    name: null,
    key_last4: '9f2c',
    service_host: 'open.bigmodel.cn',
    model: 'glm-4.6',
    checked_at: ago(2 * HOUR),
    check_error: null,
    created_at: ago(2 * DAY),
  },
]

// ---- 全部频道（`ChannelBrowseView`）----------------------------------------

/** 项目里我能看到的几个频道：默认那个、一个我没加入的、一个我已加入的。 */
export const CHANNEL_ENTRIES: ChannelEntry[] = [
  {
    id: 'ch-general',
    title: '综合',
    general: true,
    members_only: false,
    archived: false,
    joined: true,
    visible: true,
    description: null,
    member_count: 24,
    open_tasks: 3,
    last_activity_at: ago(2 * HOUR),
    manager: null,
    can_manage: false,
    can_administer: false,
  },
  {
    id: 'ch-lab',
    title: '实验课',
    general: false,
    members_only: false,
    archived: false,
    joined: false,
    visible: true,
    description: '每周的实验任务与提交在这里。',
    member_count: 18,
    open_tasks: 1,
    last_activity_at: ago(DAY),
    manager: 'linxia',
    can_manage: false,
    can_administer: false,
  },
  {
    id: 'ch-graders',
    title: '助教',
    general: false,
    members_only: true,
    archived: false,
    joined: true,
    visible: true,
    description: '批改与答疑。',
    member_count: 5,
    open_tasks: null,
    last_activity_at: ago(3 * HOUR),
    manager: 'bobi',
    can_manage: true,
    can_administer: false,
  },
]

// ---- 任务（`ProjectTasksView` / `ProjectOverviewView`）---------------------

const DEMO_PROJECT = 'p-course'
const DEMO_ROOM = 'ch-general'

/** 一件任务：剧本里只写要讲的那几项，其余按一件普通的、开着分支的活补齐。 */
function task(over: Partial<RoomTask> & { phrase: BoardPhrase; column: RoomTask['presentation']['column'] }): RoomTask {
  const { phrase, column, ...rest } = over
  return {
    id: 'task-1',
    project_id: DEMO_PROJECT,
    room_id: DEMO_ROOM,
    title: '整理第二章的课件',
    title_source: 'human',
    status: 'open',
    owner_handle: 'wangxiaoming',
    contributor_handles: [],
    reviewer_handle: 'linxia',
    created_by: 'wangxiaoming',
    branch_name: 'cheese/task-1',
    created_at: ago(3 * DAY),
    updated_at: ago(2 * HOUR),
    last_activity_at: ago(2 * HOUR),
    awaits_me: false,
    stalled: false,
    presentation: { column, phrase },
    ...rest,
  }
}

/** 项目里进行中的那几件：在等人、在做、在检查、还没开始，外加一件等人审的。 */
export const PROJECT_TASKS: RoomTask[] = [
  task({
    id: 'task-review',
    title: '作业 3 的参考答案',
    column: 'needs_you',
    phrase: 'awaiting_review',
    owner_handle: 'linxia',
    contributor_handles: ['wangxiaoming'],
    awaits_me: true,
    waiting_on: 'wangxiaoming',
  }),
  task({
    id: 'task-build',
    title: '整理第二章的课件',
    column: 'building',
    phrase: 'running',
    owner_handle: 'wangxiaoming',
  }),
  task({
    id: 'task-check',
    title: '部署课程网站',
    column: 'delivering',
    phrase: 'awaiting_checks',
    owner_handle: 'bobi',
    contributor_handles: ['wangxiaoming', 'linxia'],
  }),
  task({
    id: 'task-idea',
    title: '换一门课的题目板',
    column: 'building',
    phrase: 'started',
    owner_handle: 'linxia',
  }),
]

/** 一件十四天没动静、收进「已停滞」的活。 */
export const STALLED_TASK: RoomTask = task({
  id: 'task-stalled',
  title: '补上上学期的成绩单',
  column: 'building',
  phrase: 'idle',
  owner_handle: 'bobi',
  stalled: true,
  last_activity_at: ago(20 * DAY),
})

/** 一件已经关闭的活。 */
export const CLOSED_TASK: RoomTask = task({
  id: 'task-closed',
  accepted_at: ago(DAY),
  title: '把实验指导书发下去',
  column: 'done',
  phrase: 'accepted',
  status: 'closed',
  owner_handle: 'wangxiaoming',
})

/** `ProjectTasksView` 用的频道筛选表。 */
export const TASK_CHANNELS = [
  { id: 'ch-general', title: '综合', glyph: 'mdi-pound' },
  { id: 'ch-lab', title: '实验课', glyph: 'mdi-pound' },
  { id: 'ch-graders', title: '助教', glyph: 'mdi-lock-outline' },
]

/** handle → 名册上的名字。 */
export const TASK_NAMES: Record<string, string> = {
  wangxiaoming: '王小明',
  linxia: '林夏',
  bobi: '波比',
}

// ---- 最近进展（`ProjectOverviewView` 的 `progress`）------------------------

/** 最近两周的几件事：开的、做的、交付过一版、采纳的、停下的。 */
export const PROJECT_PROGRESS: ProgressItem[] = [
  {
    kind: 'version',
    at: ago(2 * HOUR),
    taskId: null,
    taskTitle: '',
    taskTitleSource: null,
    roomId: null,
    by: 'wangxiaoming',
    artifactId: 'art-1',
    artifactName: '月度报告.pdf',
    version: 3,
  },
  {
    kind: 'accepted',
    at: ago(5 * HOUR),
    taskId: 'task-build',
    taskTitle: '整理第二章的课件',
    taskTitleSource: 'human',
    roomId: 'ch-general',
    by: 'linxia',
    artifactId: null,
    artifactName: '',
    version: null,
  },
  {
    kind: 'started',
    at: ago(DAY),
    taskId: 'task-check',
    taskTitle: '部署课程网站',
    taskTitleSource: 'human',
    roomId: 'ch-general',
    by: 'bobi',
    artifactId: null,
    artifactName: '',
    version: null,
  },
  {
    kind: 'created',
    at: ago(2 * DAY),
    taskId: 'task-review',
    taskTitle: '作业 3 的参考答案',
    taskTitleSource: 'human',
    roomId: 'ch-general',
    by: 'linxia',
    artifactId: null,
    artifactName: '',
    version: null,
  },
]

/** 项目目标那份文档。 */
export const OVERVIEW_TEXT = '## 这门课在做什么\n\n整理操作系统课这一学期的课件与作业。'

// ---- 做出了什么（`ArtifactManifest` 的 `api` + 清单）-----------------------

/** 交出去的两项东西。 */
export const PROJECT_ARTIFACTS: ProjectArtifact[] = [
  { id: 'art-1', name: '月度报告.pdf', about: '每月给教研组的那一份。', version: 3, delivered_at: ago(2 * HOUR) },
  { id: 'art-2', name: '课程网站', about: '', version: 1, delivered_at: ago(3 * DAY) },
]

/** 还没有网站可发：`can_publish` 为假、没有入口，`PublishedSite` 整块不出现。 */
const SITE_INFO: ProjectSiteInfo = {
  can_publish: false,
  source_revision: null,
  candidates: [],
  site: null,
}

/** 一个空壳网站（`publish` 的返回值在预览站上不会被读到）。 */
const PUBLISHED_SITE: ProjectSite = {
  id: 'site-1',
  url: 'https://example.org/p-course/',
  source_revision: 'abc123',
  directory: 'dist',
  published_at: ago(3 * DAY),
  published_by: 'wangxiaoming',
}

/** 预览站上走一遍就完：清单的读写给一个空的实现，读回来就是那份固定的清单。 */
export function artifactApi(over: Partial<WorkspaceArtifactApi> = {}): WorkspaceArtifactApi {
  return {
    list: async (): Promise<ListPayload<ProjectArtifact>> => ({
      data: PROJECT_ARTIFACTS,
      total: PROJECT_ARTIFACTS.length,
    }),
    rename: async () => undefined,
    merge: async () => undefined,
    remove: async () => undefined,
    site: {
      read: async () => SITE_INFO,
      publish: async () => PUBLISHED_SITE,
    },
    ...over,
  }
}

/** 「芝士」设置页的示例数据，照后端写出的样子。 */
export const ASSISTANT_RULES: AssistantRules = {
  ask: ['remove_member', 'lower_role', 'delete'],
  askItems: ['发给老师的消息'],
  allowItems: ['在我负责的频道里回答问题', '在项目「机器学习课程」的频道「综合」发消息'],
  categories: [
    { code: 'remove_member', label: '移除成员' },
    { code: 'lower_role', label: '降低成员的角色' },
    { code: 'delete', label: '删除内容' },
    { code: 'to_everyone', label: '发给所有人的消息' },
  ],
}

export const ASSISTANT_ROUTINES: AssistantRoutine[] = [
  {
    id: 'r1',
    title: '每日待办',
    note: '列出用户的待办',
    when: '每天 08:00（Asia/Shanghai）',
    state: 'active',
    paused: '',
    nextRunAt: '2026-10-23T00:00:00Z',
    once: false,
    conversationId: 'k1',
    lastRun: { status: 'done', at: '2026-10-22T00:00:40Z', asked: 0, conversationId: 'c1' },
    weekCredits: 4.6,
  },
  {
    id: 'r2',
    title: '有人找我',
    note: '有人 @ 用户时读消息，起草回复',
    when: '每次有人 @ 你或回复你的支线时',
    state: 'active',
    paused: '',
    nextRunAt: null,
    once: false,
    conversationId: 'k2',
    lastRun: { status: 'done', at: '2026-10-22T09:00:18Z', asked: 1, conversationId: 'c2' },
    weekCredits: 12,
  },
  {
    id: 'r3',
    title: '周一交周报',
    note: '提醒用户交周报',
    when: '每周 周一 09:00（Asia/Shanghai）',
    state: 'paused',
    paused: 'credits',
    nextRunAt: null,
    once: false,
    conversationId: 'k3',
    lastRun: { status: 'skipped', at: '2026-10-20T01:00:00Z', asked: 0, conversationId: null },
    weekCredits: 0,
  },
  {
    id: 'r4',
    title: '提醒交开题报告',
    note: '提醒用户把开题报告发给导师',
    when: '10月22日 17:00',
    state: 'done',
    paused: '',
    nextRunAt: null,
    once: true,
    conversationId: 'c4',
    lastRun: { status: 'done', at: '2026-10-22T09:00:20Z', asked: 0, conversationId: 'c4' },
    weekCredits: 0.2,
  },
  {
    id: 'r5',
    title: '审阅接入搜索的提交',
    note: '读改动，对照任务要求列出问题',
    when: '任务「接入搜索」提交审阅时',
    state: 'active',
    paused: '',
    nextRunAt: null,
    once: true,
    conversationId: 'c5',
    lastRun: null,
    weekCredits: 0,
  },
  {
    id: 'r6',
    title: '整理退回意见',
    note: '把审阅意见整理成待办',
    when: '每次你提的任务被退回时',
    state: 'active',
    paused: '',
    nextRunAt: null,
    once: false,
    conversationId: 'c6',
    lastRun: null,
    weekCredits: 0,
  },
]

export const ASSISTANT_MEMORY: AssistantMemory = {
  enabled: true,
  items: [
    {
      name: 'ta-os-course',
      description: '在带「操作系统（2026 秋）」助教课，负责第 3、4 次实验',
      type: 'user',
      text: '本学期是操作系统课的助教，负责第 3、4 次实验的答疑和批改，助教频道在项目「操作系统（2026 秋）助教」里。',
      updatedAt: '2026-10-20T08:00:00Z',
    },
    {
      name: 'formal-to-teachers',
      description: '给老师写东西用书面语',
      type: 'feedback',
      text: '给老师的消息和邮件用书面语，不用「哈」「呀」，称呼用「老师」。她纠正过一次。',
      updatedAt: '2026-10-21T02:00:00Z',
    },
    {
      name: 'midterm-defense',
      description: '中期答辩定在 11 月 12 日',
      type: 'project',
      text: '课题组中期答辩定在 11 月 12 日（周四），PPT 和中期报告要在那之前给李老师过一遍。',
      updatedAt: '2026-10-22T09:30:00Z',
    },
  ],
}

export const ASSISTANT_RECENT: AssistantActivityItem[] = [
  {
    id: 12,
    kind: 'message',
    summary: '在频道「综合」（项目「机器学习课程」，全体成员都会看到）发消息：\n@李老师 演示视频我来负责',
    verdict: 'asked',
    stage: 'rule',
    reason: '你设置了「发给所有人的消息」需要确认',
    at: '2026-10-22T09:00:12Z',
  },
  {
    id: 11,
    kind: 'channel',
    summary: '在项目「机器学习课程」新建频道「论文阅读」',
    verdict: 'allowed',
    stage: 'low',
    reason: '',
    at: '2026-10-21T14:20:00Z',
  },
]
