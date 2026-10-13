/**
 * 「空间与题目」那一组整页条目（`catalogSpaceTasks.ts`）用的数据。
 *
 * 全按产品里的真形状写：空间照 `@/types` 的 `Space`（`spaces/detail/settings/*` 的容器
 * 递给画面的就是它），题目与报名照 `@/types` 的 `Task` / `TaskMembership`（`tasks/detail/*`
 * 的容器递的也是它们）。预览站碰不得接口层，所以容器算好的那几件在这里写死；条目只吃
 * props，数据只有这一处。
 *
 * 有两件的类型不从 API 层取：附件的 `TaskAttachmentData` 与报名最新一版的
 * `Latest`（出题人看板那张表用），两份都从承载它们的页面组件 `$props` 里取 —— demo
 * 源文件引用旧的网络层会把 `legacy-network` 的账记上新的一笔。
 */
import type { SettingsGroup } from '@/components/common/SettingsOverlay.vue'
import type {
  Space,
  SpaceCategory,
  SpaceMaterial,
  SpaceTeaching,
  Task,
  TaskMembership,
  TaskSubmissionReview,
  User,
} from '@/types'
import type { DomainGroup } from '@/types/spaces'
import type { PublishDraft } from '@/views/spaces/detail/publishDrafts'
import type BriefView from '@/views/tasks/detail/BriefView.vue'
import type RosterView from '@/views/tasks/detail/RosterView.vue'

import { t } from '@/i18n'

/** 附件那一行的形状（`BriefView` 的 `attachments` 里的一条）。 */
type TaskAttachmentData = InstanceType<typeof BriefView>['$props']['attachments'][number]
/** 报名最新一版提交的形状（`RosterView` 的 `latestByParticipant` 的值）。 */
type Latest = InstanceType<typeof RosterView>['$props']['latestByParticipant'] extends Map<number, infer L> ? L : never
/** 这道题挂在 YOJ 上那一块的形状（`RosterView` 的 `onlineJudge` 的值），同上。 */
type OnlineJudge = NonNullable<InstanceType<typeof RosterView>['$props']['onlineJudge']>

/** 一天，用毫秒写，别在心里换算。 */
const DAY = 86_400_000
/** 一个固定「现在」，让走势、逾期这类按时间算的东西每次都落同一格。 */
const NOW = Date.UTC(2026, 9, 11, 12, 0, 0)

// ---- 人（空间管理员、题目创建者、领取者）------------------------------------

const WANG: User = {
  id: 1,
  username: 'wangxiaoming',
  nickname: '王小明',
  avatarId: 11,
  intro: '教操作系统，也带嵌入式。',
  question_count: 6,
  answer_count: 24,
}

function participant(id: number, name: string, avatarId: number, intro = ''): TaskMembership['member'] {
  return { id, name, avatarId, intro }
}

// ---- 空间（基本信息 / 设置目录 / 指导 / 资料库）----------------------------

/** 基本信息那一栏要的整块空间：所有者、默认指导、可见上限都在。 */
export const SPACE_TASK_BOARD: Space = {
  id: 7,
  intro: '操作系统与体系结构这一门课的题目板。',
  name: '操作系统 2026 秋',
  avatarId: 21,
  admins: [{ role: 'OWNER', user: WANG }],
  taskTemplates: '',
  classificationTopics: [
    { id: 1, name: '进程与线程' },
    { id: 2, name: '内存管理' },
  ],
  defaultCategoryId: 31,
  visibleTaskLimit: 12,
  reviewStatus: 'APPROVED',
  teaching: { currentWeek: 6, allowedTopics: ['进程调度', '死锁'] },
}

/**
 * 设置目录左边那几栏，`IndexView` 的 `groups` —— 和 `settings/Index.vue` 里那七项一样：
 * 一项一条路由，`key` 就是路由名，`label` 走语言包。产品里这一组没有分组标题（标题位给
 * 的是空间名），所以这里也不给 `title`。
 */
export const SPACE_SETTINGS_GROUPS: SettingsGroup[] = [
  {
    key: 'space',
    items: [
      {
        key: 'SpacesDetailSettingsBasic',
        label: t('spaces.settings.tabs.basic'),
        to: { name: 'SpacesDetailSettingsBasic', params: { spaceId: '7' } },
        icon: 'mdi-information-outline',
      },
      {
        key: 'SpacesDetailSettingsCategories',
        label: t('spaces.settings.tabs.categories'),
        to: { name: 'SpacesDetailSettingsCategories', params: { spaceId: '7' } },
        icon: 'mdi-shape-outline',
      },
      {
        key: 'SpacesDetailSettingsTemplates',
        label: t('spaces.settings.tabs.templates'),
        to: { name: 'SpacesDetailSettingsTemplates', params: { spaceId: '7' } },
        icon: 'mdi-file-document-outline',
      },
      {
        key: 'SpacesDetailSettingsInviteCodes',
        label: t('spaces.settings.tabs.inviteCodes'),
        to: { name: 'SpacesDetailSettingsInviteCodes', params: { spaceId: '7' } },
        icon: 'mdi-ticket-outline',
      },
      {
        key: 'SpacesDetailSettingsDomainGroups',
        label: t('spaces.settings.tabs.domainGroups'),
        to: { name: 'SpacesDetailSettingsDomainGroups', params: { spaceId: '7' } },
        icon: 'mdi-email-outline',
      },
      {
        key: 'SpacesDetailSettingsGuidance',
        label: t('spaces.settings.tabs.guidance'),
        to: { name: 'SpacesDetailSettingsGuidance', params: { spaceId: '7' } },
        icon: 'mdi-robot-outline',
      },
      {
        key: 'SpacesDetailSettingsMaterials',
        label: t('spaces.settings.tabs.materials'),
        to: { name: 'SpacesDetailSettingsMaterials', params: { spaceId: '7' } },
        icon: 'mdi-folder-outline',
      },
    ],
  },
]

/** 这块板「给 AI 队友的指导」的默认那一份。 */
export const SPACE_TEACHING: SpaceTeaching = {
  systemPrompt: '这是一门操作系统的课。回答时先给结论，再给可以自己动手验证的那一步。',
  currentWeek: 6,
  allowedTopics: ['进程调度', '死锁'],
  avoidInCode: ['eval', '不教 pthread 以外的线程库'],
}

/** 资料库里的几份：文件、图、仅管理员的一档各来一个。 */
export const SPACE_MATERIALS: SpaceMaterial[] = [
  {
    id: 501,
    name: '实验指导书.pdf',
    type: 'file',
    visibility: 'members',
    size: 2_400_000,
    mime: 'application/pdf',
    uploaderId: 1,
    createdAt: NOW - 9 * DAY,
    downloadCount: 18,
    usedByCount: 3,
  },
  {
    id: 502,
    name: '进程调度示意.png',
    type: 'image',
    visibility: 'members',
    size: 512_000,
    mime: 'image/png',
    uploaderId: 1,
    createdAt: NOW - 6 * DAY,
    downloadCount: 7,
    usedByCount: 0,
  },
  {
    id: 503,
    name: '期末答案（仅管理员）.pdf',
    type: 'file',
    visibility: 'admins',
    size: 1_100_000,
    mime: 'application/pdf',
    uploaderId: 1,
    createdAt: NOW - 2 * DAY,
    downloadCount: 2,
    usedByCount: undefined,
  },
]

// ---- 题目与报名（说明 / 数据 / 领取者）-------------------------------------

/** 发题页要的分类、话题、域名组与模板。 */
export const SPACE_CATEGORY: SpaceCategory = {
  id: 31,
  name: '必做实验',
  description: '这门课所有同学都要交的实验。',
  displayOrder: 0,
  createdAt: NOW - 40 * DAY,
  updatedAt: NOW - 40 * DAY,
  archivedAt: null,
}

export const DOMAIN_GROUP: DomainGroup = {
  id: 61,
  spaceId: 7,
  name: '校内网络',
  description: null,
  domains: ['ustc.edu.cn', 'mail.ustc.edu.cn'],
  createdAt: NOW - 30 * DAY,
  updatedAt: NOW - 30 * DAY,
}

export const SPACE_TEMPLATES: { index: number; name: string; description: string }[] = [
  { index: 0, name: '标准实验（含报告要求）', description: '一周的实验，交报告与代码' },
  { index: 1, name: '随堂小测', description: '' },
]

/** 发题页上「从文件导入」读出的草稿：一道。 */
export const PUBLISH_DRAFT_ONE: PublishDraft[] = [
  {
    key: '0-进程调度',
    picked: true,
    name: '实现一个优先级的进程调度器',
    intro: '【PDF · 第 1 页】用给定的框架写出调度器。',
    description: {
      type: 'doc',
      content: [
        { type: 'paragraph', content: [{ type: 'text', text: '用给定的框架写出一个优先级调度器，跑通样例。' }] },
      ],
    },
    page: 1,
  },
]

/** 发题页上「从文件导入」读出的草稿：好几道，勾着的两道。 */
export const PUBLISH_DRAFT_MANY: PublishDraft[] = [
  {
    key: '0-调度',
    picked: true,
    name: '实现优先级调度器',
    intro: '【PDF · 第 1 页】',
    description: { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: '写调度器。' }] }] },
    page: 1,
  },
  {
    key: '1-死锁',
    picked: true,
    name: '死锁检测',
    intro: '【PDF · 第 2 页】',
    description: { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: '实现死锁检测。' }] }] },
    page: 2,
  },
  {
    key: '2-内存',
    picked: false,
    name: '页面置换',
    intro: '【PDF · 第 3 页】',
    description: { type: 'doc', content: [] },
    page: 3,
  },
]

/** 说明页与数据页共用的那道题。 */
export const TASK_DEMO: Task = {
  id: 9001,
  name: '实现一个优先级的进程调度器',
  intro: '用给定的框架写出调度器，跑通样例。',
  submitterType: 'USER',
  creator: WANG,
  registrationStartAt: NOW - 20 * DAY,
  deadline: NOW + 14 * DAY,
  participantLimit: 40,
  defaultDeadline: NOW + 21 * DAY,
  resubmittable: true,
  editable: true,
  description: '## 题目说明\n\n实现一个**优先级调度器**，跑通给定的三组样例。',
  submissionSchema: [{ prompt: '代码仓库地址', type: 'TEXT' }],
  participants: { total: 5, examples: [participant(101, '李雷', 12), participant(102, '韩梅梅', 13)] },
  submittable: true,
  createdAt: NOW - 20 * DAY,
  updatedAt: NOW - 3 * DAY,
  rank: 2,
  approved: 'APPROVED',
  requireRealName: false,
}

/** 这道题的材料清单（说明页那一栏）。 */
export const TASK_BRIEF_ATTACHMENTS: TaskAttachmentData[] = [
  {
    id: 7801,
    name: '实验指导书.pdf',
    size: 2_400_000,
    contentType: 'application/pdf',
    uploaderId: 1,
    downloadCount: 18,
    createdAt: NOW - 9 * DAY,
  },
  {
    id: 7802,
    name: 'starter.zip',
    size: 12_400_000,
    contentType: 'application/zip',
    uploaderId: 1,
    downloadCount: 6,
    createdAt: NOW - 9 * DAY,
  },
]

/** 报名名单：四种状态各一人，外加一个领了五天没动的（给「卡住了」那格用）。 */
export const TASK_ROSTER: TaskMembership[] = [
  {
    id: 101,
    member: participant(101, '李雷', 12, '喜欢底层'),
    createdAt: NOW - 12 * DAY,
    updatedAt: NOW - 12 * DAY,
    deadline: NOW + 9 * DAY,
    approved: 'APPROVED',
  },
  {
    id: 102,
    member: participant(102, '韩梅梅', 13),
    createdAt: NOW - 8 * DAY,
    updatedAt: NOW - 2 * DAY,
    deadline: NOW + 6 * DAY,
    approved: 'APPROVED',
  },
  {
    id: 103,
    member: participant(103, '小组 · 内核小队', 14),
    createdAt: NOW - 6 * DAY,
    updatedAt: NOW - 6 * DAY,
    deadline: NOW + 8 * DAY,
    approved: 'APPROVED',
    isTeam: true,
    team: { id: 71, name: '内核小队' },
    teamMembers: [
      { name: '赵敏', intro: '', avatarId: 15, isLeader: true },
      { name: '周芷若', intro: '', avatarId: 16, isLeader: false },
    ],
  },
  {
    id: 104,
    member: participant(104, '王小虎', 17),
    createdAt: NOW - 9 * DAY,
    updatedAt: NOW - 9 * DAY,
    deadline: NOW + 5 * DAY,
    approved: 'APPROVED',
  },
  {
    id: 105,
    member: participant(105, '陈晓', 18),
    createdAt: NOW - 1 * DAY,
    updatedAt: NOW - 1 * DAY,
    deadline: null,
    approved: 'NONE',
    applyReason: '想补做这道题。',
  },
]

/** 报名 → 最新一版提交：一人通过、一人退回、一人交了没判。 */
export const TASK_LATEST: Map<number, Latest> = new Map<number, Latest>([
  [
    101,
    {
      submissionId: 6001,
      version: 3,
      createdAt: NOW - 4 * DAY,
      review: { reviewed: true, detail: { accepted: true, score: 95, comment: '调度正确，注释清楚。' } },
    },
  ],
  [
    102,
    {
      submissionId: 6002,
      version: 1,
      createdAt: NOW - 2 * DAY,
      review: { reviewed: true, detail: { accepted: false, score: 40, comment: '没有处理优先级相同的情况。' } },
    },
  ],
  [104, { submissionId: 6003, version: 1, createdAt: NOW - 1 * DAY }],
])

/**
 * 这道题接的 YOJ 题号：五个领的人里**四个**有学号，两个过了。
 *
 * 三种格子在这上面都能看到：过了的（点得开、看得见过的那一刻）、没过的、以及
 * 名单上有、外站那边还没同步到的（`105` 不在 `results` 里，画「—」）。
 */
export const TASK_ONLINE_JUDGE: OnlineJudge = {
  problemNo: '1000',
  source: 'yoj',
  lastSyncedAt: new Date(NOW - 3 * 3_600_000).toISOString(),
  status: 'ok',
  error: '',
  results: [
    { participantId: 101, studentNo: '20210129', solved: true, acceptedAt: new Date(NOW - 5 * DAY).toISOString() },
    { participantId: 102, studentNo: '20210130', solved: false, acceptedAt: null },
    { participantId: 103, studentNo: '20210131', solved: true, acceptedAt: new Date(NOW - 2 * DAY).toISOString() },
    { participantId: 104, studentNo: '20210132', solved: false, acceptedAt: null },
  ],
}

/** 同上，但**上回去外站没看成**：`lastSyncedAt` 是那一趟的时刻，格子里的数还是更早那次成功的。 */
export const TASK_ONLINE_JUDGE_STALE: OnlineJudge = {
  ...TASK_ONLINE_JUDGE,
  lastSyncedAt: new Date(NOW - 5 * 60_000).toISOString(),
  status: 'failed',
  error: 'timeout',
}

/** 数据页的评审索引：`participantId → 最新那一版的评审`，`undefined` 是交了还没判。 */
export const TASK_REVIEWS: Map<number, TaskSubmissionReview | undefined> = new Map([
  [101, { reviewed: true, detail: { accepted: true, score: 95, comment: '调度正确，注释清楚。' } }],
  [102, { reviewed: true, detail: { accepted: false, score: 40, comment: '没有处理优先级相同的情况。' } }],
  [104, undefined],
])
