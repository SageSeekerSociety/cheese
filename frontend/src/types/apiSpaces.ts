import type { AnalyticsApproveType, Page, Space, SpaceAnnouncement, SpaceTeaching, TaskSubmission } from '@/types'

export type SpaceApplication = {
  /** `create`：新建空间待审；`open`：已有空间申请设为开放空间。 */
  kind: 'create' | 'open'
  /** 空间的 id（两种申请都是，审核也按它）。 */
  id: number
  /** `open` 申请自己的 id；同一个空间可以有好几条已审过的开放申请。 */
  requestId?: number
  /** `open` 申请要开放给的邮箱后缀。 */
  openScope?: string[]
  avatarId?: number | null
  name: string
  intro: string
  reviewStatus: 'PENDING' | 'APPROVED' | 'REJECTED'
  description: string
  owner: string | null
  reviewReason: string | null
  reviewedBy: string | null
  reviewedAt: string | null
  createdAt: string
}

export type PostSpaceRequestData = {
  name: string
  intro?: string
  avatarId?: number
  taskTemplates?: string
  visibleTaskLimit?: number | null
}

export type PutSpaceAccessRequestData = {
  mode: 'members' | 'open'
  scope: string[]
}

export type PostSpaceMemberRequestData = {
  userId: number
}

export type PatchSpaceRequestData = {
  name?: string
  intro?: string
  avatarId?: number
  taskTemplates?: string
  classificationTopics?: number[]
  defaultCategoryId?: number
  visibleTaskLimit?: number | null
  /** 这块板的默认「给 AI 队友的指导」。写上就**整份替换**（协议那套整键语义），
   *  省掉它则原样不动；`{}` 是清空，于是没有任何默认。 */
  teaching?: SpaceTeaching
}

// ---- 学情分析（§ learning analysis, step 1）--------------------------------------
//
// 一块题目板的学情分析底座：开关、知识点清单、AI 建议、加入时的告知卡。契约见
// `SpacesApi` 那几条 `learning-analysis` 方法；服务端在
// `app/api/routes/spaces_learning_analysis.py`。

/** 开关那一条（`PATCH .../learning-analysis`）：开还是关。 */
export type PatchLearningAnalysisRequestData = {
  enabled: boolean
}

/** 新建 / 改名一条知识点（`{ name }`）。 */
export type KnowledgePointRequestData = {
  name: string
}

/** AI 知识点建议的问：题目名 + 题面（纯文本，缺省空串）。 */
export type KnowledgeSuggestionRequestData = {
  name: string
  description: string
}

export type PostSpaceAnnouncementRequestData = {
  title: string
  content: string
  pinned: boolean
  /** Epoch milliseconds, in the future; null = it does not expire. */
  expiresAt: number | null
}

/** Each field present is changed; an explicit `expiresAt: null` clears the expiry. */
export type PatchSpaceAnnouncementRequestData = Partial<PostSpaceAnnouncementRequestData>

export type SpaceAnnouncementList = {
  /** Unexpired, pinned first and then newest. */
  current: SpaceAnnouncement[]
  /** Past their expiry, most recently expired first. */
  expired: SpaceAnnouncement[]
  /** How many people an announcement from the caller would notify; null unless the caller manages the space. */
  notifyCount: number | null
}

export type PostSpaceAdminRequestData = {
  userId: number
  role: 'OWNER' | 'ADMIN'
}

export type PatchSpaceAdminRequestData = {
  role: 'OWNER' | 'ADMIN'
}

export type GetSpacesResponseData = {
  spaces: Space[]
  page: Page
}

export type PostSpaceCategoryRequestData = {
  name: string
  description?: string | null
  displayOrder?: number
  /** 课程级教学配置。写上就**整份替换**（协议那套整键语义），省掉它则原样不动。 */
  teaching?: SpaceTeaching
  /** 项目集的默认队友（#944）：平台预设名。省略 = 不动；空串 = 清掉这一格的覆盖。 */
  defaultRole?: string
}

export type PatchSpaceCategoryRequestData = Partial<PostSpaceCategoryRequestData>

// Analytics Types
export type AnalyticsDistributionItem = {
  count: number
  label: string
  percentage?: number | null
  rangeStart?: null | number
  rangeEnd?: null | number
}

export type AnalyticsDistribution = {
  name?: string
  type?: 'DISCRETE' | 'CONTINUOUS'
  items: AnalyticsDistributionItem[]
}

export type AnalyticsTimeSeriesPoint = {
  bucket: number
  count: number
}

export type SpaceAnalyticsOverviewSummary = {
  spaceId: number
  from?: number
  to?: number
}

export type SpaceAnalyticsEntityMetrics = {
  taskCount: number
  publisherCount: number
  participantCount: number
  approvedParticipantCount: number
  submittedParticipantCount: number
  successfulParticipantCount: number
  participationConversionRate: number
  submissionConversionRate: number
  successRate: number
}

export type SpaceAnalyticsStudentMetrics = {
  studentCount: number
  approvedStudentCount: number
  successfulStudentCount: number
}

export type SpaceAnalyticsParticipantEntityMetrics = {
  participantCount: number
  approvedParticipantCount: number
  pendingParticipantCount: number
  disapprovedParticipantCount: number
  submittedParticipantCount: number
  successfulParticipantCount: number
}

export type SpaceAnalyticsParticipantStudentMetrics = {
  studentCount: number
  studentsWithRealNameCount: number
}

export type SpaceAnalyticsTaskDistributions = {
  byCategory: AnalyticsDistribution
  byApprovalStatus: AnalyticsDistribution
  byCompletionStatus: AnalyticsDistribution
}

export type SpaceAnalyticsParticipantDistributions = {
  byApprovalStatus: AnalyticsDistribution
  byCompletionStatus: AnalyticsDistribution
  byGrade: AnalyticsDistribution
  byMajor: AnalyticsDistribution
  byClassName: AnalyticsDistribution
  byRealNameStatus: AnalyticsDistribution
}

export type SpaceAnalyticsTrends = {
  tasksCreated: AnalyticsTimeSeriesPoint[]
  participantsJoined: AnalyticsTimeSeriesPoint[]
  submissionsCreated: AnalyticsTimeSeriesPoint[]
  successesAchieved: AnalyticsTimeSeriesPoint[]
}

export type SpaceAnalyticsParticipantTrends = {
  participantsJoined: AnalyticsTimeSeriesPoint[]
  submissionsCreated: AnalyticsTimeSeriesPoint[]
  successesAchieved: AnalyticsTimeSeriesPoint[]
}

export type SpaceAnalyticsOverview = {
  summary: SpaceAnalyticsOverviewSummary
  entityMetrics: SpaceAnalyticsEntityMetrics
  studentMetrics: SpaceAnalyticsStudentMetrics
  taskDistributions: SpaceAnalyticsTaskDistributions
  trends: SpaceAnalyticsTrends
}

export type SpaceAnalyticsAlerts = {
  pendingTaskApprovalCount: number
  pendingParticipantApprovalCount: number
  pendingSubmissionReviewCount: number
  stalledTaskCount: number
  overdueUnreviewedSubmissionCount: number
  inactivePublisherCount: number
}

export type SpaceAnalyticsPublisherMetrics = {
  publisherId: number
  publisherName: string
  taskCount: number
  participantCount: number
  approvedParticipantCount: number
  submittedParticipantCount: number
  successfulParticipantCount: number
  avgParticipantsPerTask: number
  submissionConversionRate: number
  successRate: number
  lastTaskCreatedAt: number
}

export type SpaceAnalyticsPublishers = {
  publishers: SpaceAnalyticsPublisherMetrics[]
}

export type SpaceAnalyticsPublisherSummary = {
  id: number
  name: string
}

export type SpaceAnalyticsCategorySummary = {
  id: number
  name: string
}

export type SpaceAnalyticsTask = {
  taskId: number
  taskName: string
  publisher: SpaceAnalyticsPublisherSummary
  category: SpaceAnalyticsCategorySummary
  approved: AnalyticsApproveType
  createdAt: number
  deadline?: number
  participantCount: number
  // 没设上限的题是 null；这里不画「/ 上限」那一截，别当成 0。
  participantLimit: number | null
  pendingParticipantApprovalCount: number
  approvedParticipantCount: number
  rejectedParticipantCount: number
  submittedParticipantCount: number
  pendingReviewCount: number
  resubmittableCount: number
  successfulParticipantCount: number
  failedParticipantCount: number
  submissionConversionRate: number
  successRate: number
}

export type SpaceTaskAnalytics = {
  tasks: SpaceAnalyticsTask[]
}

// 逐人那一格：「谁领了几道、走到哪一步」，以及**谁**在哪道题上领了两周没动。
// 一条领取的状态由提交与评审算出来（passed / submitted / inProgress），不看
// completion_status。团队领取按队一行，isTeam 标出来。
export type SpaceAnalyticsPerson = {
  userId: number
  name: string
  isTeam: boolean
  claims: number
  passed: number
  submitted: number
  inProgress: number
}

export type SpaceAnalyticsStalledClaim = {
  taskId: number
  taskTitle: string
  userId: number
  name: string
  isTeam: boolean
  /** When the claim started: its project was created. */
  startedAt: number
}

export type SpaceAnalyticsPeople = {
  people: SpaceAnalyticsPerson[]
  stalled: SpaceAnalyticsStalledClaim[]
}

export type SpaceAnalyticsParticipants = {
  summary: SpaceAnalyticsOverviewSummary
  entityMetrics: SpaceAnalyticsParticipantEntityMetrics
  studentMetrics: SpaceAnalyticsParticipantStudentMetrics
  distributions: SpaceAnalyticsParticipantDistributions
  trends: SpaceAnalyticsParticipantTrends
}

// 学习维度 (管理员看板 · 学习): 成员说过的话、卡点、讲解提纲。读的是成员项目里的
// 对话，不是赛题与报名表，所以类型和上面那一组分开。
export type SpaceLearningKnowledgePoint = {
  categoryId: number
  name: string
}

export type SpaceLearningFilters = {
  knowledgePoints: SpaceLearningKnowledgePoint[]
  projectCount: number
}

/** 一条能点回原文的成员发言 —— 队列、发言列表、提纲里的摘录都是它。 */
export type SpaceLearningExcerpt = {
  blockId: string
  topicId: string
  projectId: string
  /** 本空间固定代号（如 A7F3）：老师只看全班整体，发言不标是谁。 */
  memberCode: string
  topicTitle: string
  createdAt: number
  quote: string
  /** 知识点是**名字**（没归类就是 null）；筛选那一维传的是分类 id，两者不同。 */
  knowledgePoint?: string | null
}

export type SpaceLearningQuestion = SpaceLearningExcerpt & {
  projectName: string
}

export type SpaceLearningQuestions = {
  questions: SpaceLearningQuestion[]
  total: number
}

export type SpaceLearningStuckPoint = {
  knowledgePoint?: string | null
  studentCount: number
  projectCount: number
  questionCount: number
  latestAt: number
  example: SpaceLearningQuestion
}

export type SpaceLearningQueues = {
  /** 平台没有记录这个信号，所以只有 available: false + reason，没有内容。 */
  reviewFlag: {
    available: boolean
    reason: string
    items: SpaceLearningQuestion[]
  }
  /** 按「多少个成员撞上」排好的共性卡点。 */
  stuckPoints: SpaceLearningStuckPoint[]
}

export type SpaceLearningOutlineSection = {
  knowledgePoint: string
  session: number
  excerpts: SpaceLearningExcerpt[]
}

export type SpaceLearningOutline = {
  title: string
  sections: SpaceLearningOutlineSection[]
  /** 勾中但已经读不到的发言 id。 */
  missing: string[]
}

// 学情分析 · 老师看全班整体（§ learning analysis, step 3，
// `app/api/routes/spaces_learning_class.py`）。只有整体：没有成员 id、handle 或名字；
// 例句只标本空间固定代号。``analysed: false`` 是「不足 3 人，不分析」，那时人数都是 null。
export type LearningClassTaskCompletion = {
  taskId: number
  name: string
  analysed: boolean
  notStarted: number | null
  inProgress: number | null
  submitted: number | null
  passed: number | null
}

export type LearningClassFocusPoint = {
  knowledgePointId: number
  name: string
  stuck: number
  involved: number
}

export type LearningClassOverview = {
  enabled: boolean
  minPeople: number
  updatedAt: number | null
  members: number
  completion: { rate: number | null; submitted: number; expected: number }
  messagesThisWeek: number
  weakPoints: number
  tasks: LearningClassTaskCompletion[]
  focus: LearningClassFocusPoint[]
}

export type LearningClassQuadrant = 'both' | 'hard' | 'focus' | 'normal'

export type LearningClassPoint = {
  id: number
  name: string
  analysed: boolean
  asked: number | null
  involved: number | null
  stuck: number | null
  stuckRate: number | null
  quadrant: LearningClassQuadrant | null
}

export type LearningClassPoints = {
  enabled: boolean
  minPeople: number
  members: number
  askedRatio: number
  stuckRatio: number
  askedThreshold: number
  points: LearningClassPoint[]
}

export type LearningCodedQuote = { code: string; quote: string; taskName?: string | null }

export type LearningClassMisconception = {
  id: number
  label: string
  people: number
  examples: LearningCodedQuote[]
}

export type LearningClassPointDetail = {
  enabled: boolean
  minPeople: number
  knowledgePoint: { id: number; name: string }
  analysed: boolean
  members: number
  involved: number | null
  stuck: number | null
  newThisWeek: number | null
  tasks: string[]
  mastery: { mastered: number; shaky: number; weak: number; untouched: number } | null
  misconceptions: LearningClassMisconception[]
  hiddenMisconceptions: number
  advice: { text: string; generatedAt: number | null } | null
}

export type LearningClassCluster = {
  id: number
  label: string
  knowledgePointId: number | null
  knowledgePointName: string | null
  people: number
  asks: number
  trend: 'up' | 'down' | 'new' | null
  example: LearningCodedQuote | null
}

export type LearningClassQuestions = {
  enabled: boolean
  minPeople: number
  totalQuestions: number
  totalClusters: number
  hiddenClusters: number
  clusters: LearningClassCluster[]
}

export type LearningExcerptMessage = { role: 'member' | 'agent'; code: string | null; text: string; at: number | null }

export type LearningExcerpt = {
  code: string
  taskName: string
  at: number | null
  messages: LearningExcerptMessage[]
  /** 「查看原文」的坐标：只有读得了那个项目的人（导师）才有。 */
  source: { topicId: string; projectId: string; blockId: string } | null
}

export type LearningClassExcerpts = {
  enabled: boolean
  minPeople: number
  analysed: boolean
  title: string
  excerpts: LearningExcerpt[]
}

export type LearningClassWords = {
  enabled: boolean
  minPeople: number
  windowDays: number
  words: Array<{ word: string; count: number; people: number; isNew: boolean; rising: boolean }>
  errors: Array<{ line: string; count: number; people: number }>
}

export type LearningClassTaskDifficulty = {
  taskId: number
  name: string
  analysed: boolean
  claims: number | null
  completionRate: number | null
  avgTurns: number | null
  avgHours: number | null
  stuckRate: number | null
  answerSeekers: number | null
}

export type LearningClassTasks = { enabled: boolean; minPeople: number; tasks: LearningClassTaskDifficulty[] }

export type LearningQuestionKind = 'concept' | 'error' | 'answer' | 'verify' | 'other'

export type LearningClassBehavior = {
  enabled: boolean
  minPeople: number
  analysed: boolean
  totalQuestions: number
  kinds: Array<{ kind: LearningQuestionKind; count: number; share: number }>
  /** 时段的列名（本地钟点，如 `0-8`）。 */
  slots: string[]
  /** 7 行（周一到周日）× slots 列；不足 3 人的格子为 null。 */
  heat: Array<Array<number | null>>
  dependency: { low: number; mid: number; high: number } | null
}

export type LearningClassTrend = {
  enabled: boolean
  minPeople: number
  /** 每周的星期一，ISO 日期。 */
  weeks: string[]
  series: Array<{ knowledgePointId: number; name: string; values: Array<number | null> }>
}

export type LearningExportFormat = 'pdf' | 'xlsx' | 'docx'
export type LearningExportSection = 'tasks' | 'points' | 'advice' | 'excerpts'

export type LearningExportRequest = {
  format: LearningExportFormat
  sections: LearningExportSection[]
  knowledgePointId?: number | null
}

// 「我发布的题目」那几个类型挪到领域类型 `@/types/spaces.ts` 了：题目列表拆出来的那半张
// 画面只认 props、不连网络层，不能从这儿取类型。这里原样转出去，对外的 import 路径不变。
export type {
  AnalyticsApproveType,
  SpaceMyPublishedTask,
  SpaceMyPublishedTaskCategory,
  SpaceMyPublishedTasks,
  SpaceTaskVisibilityStatus,
} from '@/types'
export type AnalyticsRealNameType = 'all' | 'with' | 'without'
export type AnalyticsCompletionType =
  | 'NOT_SUBMITTED'
  | 'PENDING_REVIEW'
  | 'REJECTED_RESUBMITTABLE'
  | 'FAILED'
  | 'SUCCESS'
export type AnalyticsGroupBy = 'day' | 'week' | 'month'
export type AnalyticsSortOrder = 'asc' | 'desc'

// Domain Group Types
export type PostSpaceDomainGroupRequestData = {
  name: string
  description?: string | null
  domains: string[]
}

export type PatchSpaceDomainGroupRequestData = {
  name?: string
  description?: string | null
  domains?: string[]
}

// Legacy aliases kept temporarily for generic chart reuse.
export type AnalyticsStudentStatistics = {
  totalStudents: number
  totalStudentsWithRealName: number
  gradeDistribution: AnalyticsDistribution
  majorDistribution: AnalyticsDistribution
  classNameDistribution: AnalyticsDistribution
}

export type SpaceAnalyticsTasksData = {
  taskCategoryDistribution: AnalyticsDistribution
  taskStatusDistribution: AnalyticsDistribution
  participantStatusDistribution: AnalyticsDistribution
  successStudentStatistics: AnalyticsStudentStatistics
  unsuccessStudentStatistics: AnalyticsStudentStatistics
  rankDistribution: AnalyticsDistribution
}

export type PublisherParticipation = {
  publisherId: number
  publisherName: string
  participants: number
  completedUsers: number
  taskCount: number
}

/**
 * 一门课的作业与验收：一行是「谁的哪份作业」。
 *
 * 它是 `TaskSubmission` 加上三个定位用的字段 —— `taskId` / `taskTitle` 说明这是
 * 哪道作业，`participantId` 是既有的提交与评审接口要的那条报名记录 id。
 */
export type SpaceSubmissionRow = TaskSubmission & {
  taskId: number
  taskTitle: string
  participantId: number
}

/** 课程那一屏的数字。四个数来自同一个口径（每人只算最新一版）。 */
export type SpaceSubmissionSummary = {
  participants: number
  submissions: number
  pendingReview: number
  missing: number
}

export type SpaceSubmissionQueue = {
  submissions: SpaceSubmissionRow[]
  summary: SpaceSubmissionSummary
  page: Page
}
