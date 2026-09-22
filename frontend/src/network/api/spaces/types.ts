import type { Page, Space, TaskSubmitterType } from '@/types'

export type SpaceApplication = {
  id: number
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
  announcements?: string
  taskTemplates?: string
  visibleTaskLimit?: number | null
}

export type PostSpaceJoinRequestData = {
  code: string
}

/**
 * The course link's payload. The link decides only WHERE the student lands,
 * never what he may see, so this carries the code and nothing else.
 */
export type PostSpaceEnrollRequestData = {
  code?: string
}

/** What opening a course link leaves behind: the board, and his project in it. */
export type SpaceEnrollment = {
  space: { id: number; name: string }
  /** null when the course has nothing to hang a project on yet. */
  project: { id: string; name: string; root_topic_id: string | null } | null
}

/** Teacher-side: the link to copy into the group chat, and the code inside it. */
export type SpaceCourseLink = {
  path: string
  code: string
  maxUses: number
  useCount: number
  expiresAt: number | null
}

export type PostSpaceMemberRequestData = {
  userId: number
}

export type PostSpaceInviteCodeRequestData = {
  maxUses?: number
  /** Epoch milliseconds; omit for a code that never expires. */
  expiresAt?: number | null
}

export type PatchSpaceRequestData = {
  name?: string
  intro?: string
  avatarId?: number
  announcements?: string
  taskTemplates?: string
  classificationTopics?: number[]
  defaultCategoryId?: number
  visibleTaskLimit?: number | null
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

export type SpaceAnalyticsParticipants = {
  summary: SpaceAnalyticsOverviewSummary
  entityMetrics: SpaceAnalyticsParticipantEntityMetrics
  studentMetrics: SpaceAnalyticsParticipantStudentMetrics
  distributions: SpaceAnalyticsParticipantDistributions
  trends: SpaceAnalyticsParticipantTrends
}

// 学习维度 (教师看板 · 学习): 学生说过的话、卡点、讲解提纲。读的是学生项目里的
// 对话，不是赛题与报名表，所以类型和上面那一组分开。
export type SpaceLearningStudent = {
  handle: string
  name: string
}

export type SpaceLearningKnowledgePoint = {
  categoryId: number
  name: string
}

export type SpaceLearningFilters = {
  students: SpaceLearningStudent[]
  knowledgePoints: SpaceLearningKnowledgePoint[]
  projectCount: number
}

/** 一条能点回原文的学生发言 —— 队列、发言列表、提纲里的摘录都是它。 */
export type SpaceLearningExcerpt = {
  blockId: string
  topicId: string
  projectId: string
  student: string
  studentName: string
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
  /** 按「多少个学生撞上」排好的共性卡点。 */
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

export type SpaceMyPublishingOverview = {
  spaceId: number
  taskCount: number
  approvedTaskCount: number
  pendingTaskApprovalCount: number
  disapprovedTaskCount: number
  participantCount: number
  approvedParticipantCount: number
  pendingParticipantApprovalCount: number
  submittedParticipantCount: number
  pendingReviewCount: number
  successfulParticipantCount: number
}

export type SpaceMyPublishedTaskCategory = {
  id: number
  name: string
}

export type SpaceMyPublishedTask = {
  taskId: number
  taskName: string
  category: SpaceMyPublishedTaskCategory
  approved: AnalyticsApproveType
  visibilityStatus: SpaceTaskVisibilityStatus
  isVisible: boolean
  createdAt: number
  publishedAt?: number | null
  endedAt?: number | null
  deadline?: number | null
  participantCount: number
  approvedParticipantCount: number
  pendingParticipantApprovalCount: number
  submittedParticipantCount: number
  pendingReviewCount: number
  successfulParticipantCount: number
  failedParticipantCount: number
  submissionConversionRate: number
  successRate: number
  latestSubmissionAt?: number | null
}

export type SpaceMyPublishedTasks = {
  tasks: SpaceMyPublishedTask[]
}

export type SpaceMyParticipatingOverview = {
  spaceId: number
  participationCount: number
  approvedParticipationCount: number
  pendingApprovalCount: number
  awaitingSubmissionCount: number
  pendingReviewCount: number
  resubmittableCount: number
  successfulCount: number
  failedCount: number
}

export type SpaceMyParticipationPublisher = {
  id: number
  name: string
}

export type SpaceMyParticipationCategory = {
  id: number
  name: string
}

export type SpaceMyParticipation = {
  taskId: number
  taskName: string
  publisher: SpaceMyParticipationPublisher
  category: SpaceMyParticipationCategory
  participationId: number
  identityType: TaskSubmitterType
  teamName?: string | null
  approved: AnalyticsApproveType
  completionStatus: AnalyticsCompletionType
  canSubmit: boolean
  joinedAt: number
  deadline?: number | null
  latestSubmissionAt?: number | null
  latestReviewAccepted?: boolean | null
  latestReviewScore?: number | null
}

export type SpaceMyParticipations = {
  participations: SpaceMyParticipation[]
}

export type AnalyticsApproveType = 'NONE' | 'APPROVED' | 'DISAPPROVED'
export type SpaceTaskVisibilityStatus =
  | 'PENDING_APPROVAL'
  | 'REJECTED'
  | 'APPROVED_HIDDEN'
  | 'APPROVED_VISIBLE'
  | 'ENDED'
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

// ── 教学单元（课程的时间线） ──────────────────────────────────────────────────

export type TeachingUnit = {
  id: number
  spaceId: number
  week: number
  title: string
  summary: string
  knowledgePointIds: number[]
  materialIds: number[]
  assignmentTaskId: number | null
  publishedAt: number | null
  dueAt: number | null
  /** 这一周的小测；null = 没有。学生首页靠它决定要不要给「本周有小测」那个入口。 */
  quizId: number | null
}

export type GetTeachingUnitsResponseData = {
  units: TeachingUnit[]
  canTeach: boolean
}

export type PostTeachingUnitRequestData = {
  week: number
  title: string
  summary?: string
  knowledgePointIds?: number[]
  materialIds?: number[]
  assignmentTaskId?: number | null
  dueAt?: number | null
  published?: boolean
}

export type PatchTeachingUnitRequestData = {
  week?: number
  title?: string
  summary?: string
  knowledgePointIds?: number[]
  materialIds?: number[]
  assignmentTaskId?: number | null
  clearAssignment?: boolean
  dueAt?: number | null
  clearDueAt?: boolean
  published?: boolean
}

/** 这道题怎么答、怎么判，由 `kind` 决定（见后端 quiz_models）。 */
export type QuizQuestionKind = 'SINGLE_CHOICE' | 'MULTIPLE_CHOICE' | 'TRUE_FALSE' | 'FILL_BLANK' | 'SHORT_ANSWER'

export type Quiz = {
  id: number
  spaceId: number
  unitId: number
  title: string
  dueAt: number | null
}

export type QuizQuestion = {
  id: number
  position: number
  kind: QuizQuestionKind
  prompt: string
  options: string[]
  points: number
  /** 只有老师那份有 —— 学生的载荷里没有这一格（答案键从不发给学生）。 */
  answer?: unknown
}

export type QuizAttempt = {
  id: number
  userId: number
  submittedAt: number
  gradedAt: number | null
  /** 学生那份才有：已经判出来的分，与「还有题等着老师判」。 */
  score?: number
  pendingReview?: boolean
}

export type QuizAnswer = {
  questionId: number
  response: unknown
  awardedPoints: number | null
  comment: string
  needsReview: boolean
}

/** 一行「谁交的」。老师的载荷里才有。 */
export type QuizSubmission = {
  attemptId: number
  userId: number
  submittedAt: number
  gradedAt: number | null
  score: number
  maxScore: number
  user?: { id: number; username: string; nickname?: string }
}

/** 复核队列里的一项：一道等着老师判的简答。 */
export type QuizReviewItem = {
  answerId: number
  attemptId: number
  userId: number
  submittedAt: number
  questionId: number
  kind: QuizQuestionKind
  prompt: string
  referenceAnswer: unknown
  points: number
  response: unknown
  user?: { id: number; username: string; nickname?: string }
}

/**
 * 小测页的全部东西。`quiz` 为 null = 这一周还没有小测（学生什么都看不到）。
 *
 * **学生那份没有 `answer`，也没有 `submissions` / `reviewQueue`** —— 分叉在服务端
 * 一次做完，前端不自己判一次权限。
 */
export type TeachingQuizData = {
  quiz: Quiz | null
  canTeach?: boolean
  questions?: QuizQuestion[]
  maxScore?: number
  myAttempt?: QuizAttempt | null
  myAnswers?: QuizAnswer[]
  submissions?: QuizSubmission[]
  reviewQueue?: QuizReviewItem[]
}

export type PostQuizRequestData = {
  title: string
  dueAt?: number | null
}

export type PatchQuizRequestData = {
  title?: string
  dueAt?: number | null
  clearDueAt?: boolean
}

export type PostQuizQuestionRequestData = {
  kind: QuizQuestionKind
  prompt: string
  options?: string[]
  answer?: unknown
  points?: number
}

export type PatchQuizQuestionRequestData = PostQuizQuestionRequestData

export type PostQuizAttemptRequestData = {
  answers: { questionId: number; response: unknown }[]
}

export type PatchQuizAnswerRequestData = {
  points: number
  comment?: string
}
