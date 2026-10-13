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
import type { ProjectArtifact } from '@/api'
import type { BoardPhrase, ListPayload, MyTeam, ProjectSite, ProjectSiteInfo, RoomTask } from '@/cx_types'
import type { MemberFaces } from '@/lib/memberFaces'
import type { NotificationPreferences } from '@/lib/notificationPreferences'
import type { UserRefTarget } from '@/lib/userRef'
import type { ChannelEntry } from '@/types/channelDirectory'
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
    avatarId: null,
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
    avatarId: null,
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

/** 一个已关联的第三方账号。 */
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
  setupStep: 'qr' as const,
  verificationCode: '',
}

// ---- 这台设备（`ThisDeviceView`）-------------------------------------------

/** 可供选择的团队：一个个人团队、一个真团队。 */
export const DEVICE_TEAMS: MyTeam[] = [
  { id: 1, handle: 'wangxiaoming', name: '王小明', personal: true },
  { id: 2, handle: 'data', name: '数据组' },
]

// ---- 浏览频道（`ChannelBrowseView`）----------------------------------------

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
    column: 'not_started',
    phrase: 'discussing',
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

/** 同一张名册，供画人用的表：显示名 / 自选头像 id / 是不是 AI 队友。波比是队友。 */
export const TASK_FACES: MemberFaces = {
  wangxiaoming: { name: '王小明', avatarId: null, agent: false },
  linxia: { name: '林夏', avatarId: null, agent: false },
  bobi: { name: '波比', avatarId: null, agent: true },
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
