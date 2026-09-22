import type { Page, Space, TaskSubmitterType } from '@/types'

export type PostSpaceRequestData = {
  name: string
  intro?: string
  avatarId?: number
  announcements?: string
  taskTemplates?: string
  visibleTaskLimit?: number | null
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

// ── 空间级共享额度池 ────────────────────────────────────────────────────────
// 一个空间（课程）买一份额度池，空间下所有项目共用，先到先得。见
// backend/app/domain/usage/space_pool.py。金额单位是 credit。

export type SpaceComputePoolProject = {
  project_id: string
  name: string
  cost_usd: number
  total_tokens: number
  turns: number
}

export type SpaceComputePool = {
  space_id: number
  //: 这个空间有没有买过池子。false 时所有项目照旧不限量。
  has_pool: boolean
  credits_total: number
  credits_used: number
  credits_remaining: number
  //: used / total，0..1；没有池子时为 0。
  credits_ratio: number
  //: 提醒阈值（当前 0.8），后端给的，前端别自己写死。
  alert_ratio: number
  exhausted: boolean
  //: 需要提醒老师：已过阈值或已用完。
  needs_attention: boolean
  projects: SpaceComputePoolProject[]
}

export type FundSpaceComputePoolRequestData = {
  //: 充值额度，增量而不是「设成多少」；必须大于 0。
  credits: number
}
