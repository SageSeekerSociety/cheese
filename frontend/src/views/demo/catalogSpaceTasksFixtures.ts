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
  TaskDraftSummary,
  TaskMembership,
  TaskSubmissionReview,
  User,
} from '@/types'
import type { DomainGroup } from '@/types/spaces'
import type { TaskVersionData } from '@/types/taskFiles'
import type BriefView from '@/views/tasks/detail/BriefView.vue'
import type RosterView from '@/views/tasks/detail/RosterView.vue'

import { t } from '@/i18n'

/** 附件那一行的形状（`BriefView` 的 `attachments` 里的一条）。 */
type TaskAttachmentData = InstanceType<typeof BriefView>['$props']['attachments'][number]
/** 报名最新一版提交的形状（`RosterView` 的 `latestByParticipant` 的值）。 */
type Latest = InstanceType<typeof RosterView>['$props']['latestByParticipant'] extends Map<number, infer L> ? L : never

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
  teaching: { mode: 'guide', systemPrompt: '先弄清学生卡在哪一步，再决定怎么帮。' },
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
    ],
  },
]

/** 这块板「给 AI 队友的指导」的默认那一份。 */
export const SPACE_TEACHING: SpaceTeaching = {
  mode: 'guide',
  systemPrompt: '这是一门操作系统的课。回答时先给结论，再给可以自己动手验证的那一步。不要用 pthread 以外的线程库。',
}

/** 资料库里的几份：文件、图、文本、仅管理员的一档各来一个。 */
export const SPACE_MATERIALS: SpaceMaterial[] = [
  {
    id: 504,
    name: '实验三要求.md',
    type: 'file',
    visibility: 'members',
    size: 1_800,
    mime: 'text/markdown',
    uploaderId: 2,
    uploaderName: '王助教',
    createdAt: NOW - 3 * 60 * 60 * 1000,
    downloadCount: 4,
    usedByCount: 1,
  },
  {
    id: 501,
    name: '实验指导书.pdf',
    type: 'file',
    visibility: 'members',
    size: 2_400_000,
    mime: 'application/pdf',
    uploaderId: 1,
    uploaderName: '陈老师',
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
    uploaderName: '陈老师',
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
    uploaderName: '陈老师',
    createdAt: NOW - 2 * DAY,
    downloadCount: 2,
    usedByCount: undefined,
  },
]

/** 资料库里点开「实验三要求.md」时预览读到的内容。 */
export const SPACE_MATERIAL_TEXT = `# 实验三：进程调度

用 C 实现时间片轮转调度，时间片可配置。

1. 读入进程列表（到达时间、运行时间）。
2. 输出每个进程的完成时间和周转时间。
3. 比较时间片为 2 和 8 时的平均周转时间，写在报告里。
`

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

/** 发题页顶栏「草稿」里的几份：两份新题目，一份已发布题目的修改。 */
export const PUBLISH_DRAFTS: TaskDraftSummary[] = [
  {
    id: 41,
    spaceId: 7,
    taskId: null,
    taskName: null,
    name: 'Lab 3 缓存模拟器',
    source: 'Lab3-缓存模拟器.pdf',
    conversationId: null,
    version: 6,
    updatedAt: new Date(NOW - 2 * 60_000).toISOString(),
    missing: [],
  },
  {
    id: 42,
    spaceId: 7,
    taskId: null,
    taskName: null,
    name: 'Lab 4 矩阵转置优化',
    source: '第二单元实验与作业.pdf',
    conversationId: null,
    version: 2,
    updatedAt: new Date(NOW - 40 * 60_000).toISOString(),
    missing: [],
  },
  {
    id: 38,
    spaceId: 7,
    taskId: 88,
    taskName: 'Lab 2 二进制炸弹',
    name: 'Lab 2 二进制炸弹',
    source: '',
    conversationId: null,
    version: 1,
    updatedAt: new Date(NOW - 3 * DAY).toISOString(),
    missing: [],
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
  participants: { total: 5, approved: 4, examples: [participant(101, '李雷', 12), participant(102, '韩梅梅', 13)] },
  submittable: true,
  createdAt: NOW - 20 * DAY,
  updatedAt: NOW - 3 * DAY,
  rank: 2,
  approved: 'APPROVED',
  requireRealName: false,
}

/** 这道题的材料清单（说明页那一栏）。 */
export const TASK_BRIEF_ATTACHMENTS: TaskAttachmentData[] = [
  { name: '实验指导书.pdf', size: 2_400_000, addedAt: NOW - 9 * DAY },
  { name: 'starter.zip', size: 12_400_000, addedAt: NOW - 9 * DAY },
]

/** 历史版本：发布、加了一份数据、换了数据又删了草稿；最早那一版记版本时还没记文件。 */
export const TASK_VERSIONS: TaskVersionData[] = [
  {
    number: 3,
    at: NOW - 1 * DAY,
    author: { id: 1, username: 'teacher', nickname: '王老师' },
    changes: { files: { replaced: ['数据.csv'], removed: ['草稿.txt'] } },
    files: [
      { name: '实验指导书.pdf', size: 2_400_000 },
      { name: '数据.csv', size: 48_000 },
      { name: '题目说明.md', size: 1_200 },
    ],
  },
  {
    number: 2,
    at: NOW - 4 * DAY,
    author: { id: 1, username: 'teacher', nickname: '王老师' },
    changes: { description: true, files: { added: ['数据.csv', '草稿.txt'] } },
    files: [
      { name: '实验指导书.pdf', size: 2_400_000 },
      { name: '数据.csv', size: 40_000 },
      { name: '草稿.txt', size: null },
      { name: '题目说明.md', size: 1_100 },
    ],
  },
  {
    number: 1,
    at: NOW - 9 * DAY,
    author: { id: 1, username: 'teacher', nickname: '王老师' },
    changes: { published: true },
    files: null,
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

/** 数据页的评审索引：`participantId → 最新那一版的评审`，`undefined` 是交了还没判。 */
export const TASK_REVIEWS: Map<number, TaskSubmissionReview | undefined> = new Map([
  [101, { reviewed: true, detail: { accepted: true, score: 95, comment: '调度正确，注释清楚。' } }],
  [102, { reviewed: true, detail: { accepted: false, score: 40, comment: '没有处理优先级相同的情况。' } }],
  [104, undefined],
])

/** 芝士面板：正在写一份草稿的那一轮。 */
export const PANEL_LIVE = {
  messages: [
    {
      role: 'user' as const,
      text: '根据 Lab3-缓存模拟器.pdf 出一道题',
      at: new Date(NOW).toISOString(),
      files: ['Lab3-缓存模拟器.pdf'],
    },
  ],
  steps: [
    { key: 't1', verb: '思考了 3 秒', arg: '', state: 'done' as const, thinking: true },
    { key: 'c1', verb: '读取文件', arg: '1-12', state: 'done' as const },
    { key: 'c2', verb: '查看空间题目', arg: 'Lab', state: 'done' as const },
    { key: 'c3', verb: '填写题目', arg: 'Lab 3 缓存模拟器', state: 'run' as const },
  ],
}

/** 芝士面板：答完的一轮，改动卡、待你确认、发布结果都在。 */
export const PANEL_DONE = {
  messages: [
    {
      role: 'user' as const,
      text: '根据 Lab3-缓存模拟器.pdf 出一道题',
      at: new Date(NOW).toISOString(),
      files: ['Lab3-缓存模拟器.pdf'],
    },
    {
      role: 'assistant' as const,
      text: '已按文件填写名称、描述、提交内容和评分要点。截止时间按附件中的「第 9 周周日前」换算，需要你确认。',
      at: new Date(NOW).toISOString(),
      steps: [
        { key: 'c1', verb: '读取文件', arg: '1-12', state: 'done' as const },
        { key: 'c2', verb: '查看空间题目', arg: 'Lab', state: 'done' as const },
        { key: 'c3', verb: '填写题目', arg: 'Lab 3 缓存模拟器', state: 'done' as const },
      ],
      changes: [
        {
          id: 1,
          draft: 41,
          created: false,
          name: 'Lab 3 缓存模拟器',
          canUndo: true,
          rows: [
            { key: 'name', label: '名称', value: 'Lab 3 缓存模拟器', undone: false },
            {
              key: 'description',
              label: '描述',
              value: '用 C 写一个缓存模拟器 csim，读入 valgrind 生成的访存记录',
              undone: false,
            },
            { key: 'rubric', label: '评分要点', value: '3 项，合计 100 分', undone: false },
            { key: 'deadline', label: '截止日期', value: '2026-11-01 23:59', undone: true },
          ],
          notes: [{ key: 'deadline', label: '截止日期', text: '按附件「第 9 周周日前」换算为 11 月 1 日' }],
        },
      ],
      results: [{ taskId: 88, title: '已发布「Lab 3 缓存模拟器」', sub: '待管理员审核' }],
    },
  ],
}

/** 只附一份文件不打字：第一道填进眼前这份，另两道各建一份，各收成一行。 */
export const PANEL_SPLIT = {
  messages: [
    {
      role: 'user' as const,
      text: '',
      at: new Date(NOW).toISOString(),
      files: ['实验室科研课题-2026秋.pdf'],
    },
    {
      role: 'assistant' as const,
      text: '文件里有三个课题：课题 A 填进了这份草稿，B、C 各建了一份。报名截止按「11 月 15 日」填写，结题日期写进了描述。',
      at: new Date(NOW).toISOString(),
      steps: [
        { key: 'c1', verb: '读取文件', arg: '1-4', state: 'done' as const },
        { key: 'c2', verb: '填写题目', arg: '基于 eBPF 的容器网络延迟分析工具', state: 'done' as const },
        { key: 'c3', verb: '填写题目', arg: '大模型推理服务中 KV Cache 换出策略的评测', state: 'done' as const },
        { key: 'c4', verb: '填写题目', arg: '校园网 DNS 日志中的异常域名检测', state: 'done' as const },
      ],
      changes: [
        {
          id: 1,
          draft: 41,
          created: false,
          name: '基于 eBPF 的容器网络延迟分析工具',
          canUndo: true,
          ordinal: 1,
          rows: [
            { key: 'name', label: '名称', value: '基于 eBPF 的容器网络延迟分析工具', undone: false },
            { key: 'form', label: '参与方式', value: '团队', undone: false },
            { key: 'team_max', label: '最多人数', value: '2', undone: false },
            { key: 'days_to_submit', label: '完成期限', value: '60 天', undone: false },
            { key: 'attachments', label: '附件', value: '实验室科研课题-2026秋.pdf', undone: false },
          ],
          notes: [{ key: 'deadline', label: '截止日期', text: '原文是报名截止，结题日期 2027-01-10 写在描述里' }],
        },
        {
          id: 2,
          draft: 42,
          created: true,
          collapsed: true,
          name: '大模型推理服务中 KV Cache 换出策略的评测',
          canUndo: false,
          ordinal: 2,
          rows: [
            { key: 'name', label: '名称', value: '大模型推理服务中 KV Cache 换出策略的评测', undone: false },
            { key: 'difficulty', label: '难度', value: '高级', undone: false },
          ],
          notes: [],
        },
        {
          id: 3,
          draft: 43,
          created: true,
          collapsed: true,
          name: '校园网 DNS 日志中的异常域名检测',
          canUndo: false,
          ordinal: 3,
          rows: [{ key: 'name', label: '名称', value: '校园网 DNS 日志中的异常域名检测', undone: false }],
          notes: [],
        },
      ],
      results: [],
    },
  ],
}
