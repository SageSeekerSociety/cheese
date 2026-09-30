import type { Page, Space, SpaceAnnouncement, SpaceTeaching, TaskSubmission } from '@/types'

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
  taskTemplates?: string
  visibleTaskLimit?: number | null
}

export type PostSpaceJoinRequestData = {
  code: string
}

export type PostSpaceMemberRequestData = {
  userId: number
}

export type PostSpaceInviteCodeRequestData = {
  maxUses?: number
  /** Epoch milliseconds; omit for a code that never expires. */
  expiresAt?: number | null
  /** 说明 —— 这张码给谁、干什么用. Omitted or blank stores no note. */
  note?: string | null
}

export type PatchSpaceInviteCodeRequestData = {
  maxUses?: number
  /** Absent leaves the date alone; an explicit null makes the code never expire. */
  expiresAt?: number | null
  /**
   * Absent leaves the 说明 alone; null (or a blank string) clears it. The two
   * are deliberately different requests — correcting a note must not be the
   * same thing as deleting it.
   */
  note?: string | null
}

export type PatchSpaceRequestData = {
  name?: string
  intro?: string
  avatarId?: number
  taskTemplates?: string
  classificationTopics?: number[]
  defaultCategoryId?: number
  visibleTaskLimit?: number | null
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
  claimedAt: number
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

/** 一条能点回原文的成员发言 —— 队列、发言列表、提纲里的摘录都是它。 */
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
  /** 领取人数上限；`null` = 不限（真库用 0 表示不限，接口一律折成 null）。 */
  participantLimit: number | null
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
