import type {
  AnalyticsApproveType,
  AnalyticsCompletionType,
  AnalyticsGroupBy,
  AnalyticsRealNameType,
  AnalyticsSortOrder,
  LearningClassBehavior,
  LearningClassExcerpts,
  LearningClassOverview,
  LearningClassPointDetail,
  LearningClassPoints,
  LearningClassQuestions,
  LearningClassTasks,
  LearningClassTrend,
  LearningClassWords,
  LearningExportRequest,
  LearningPushResult,
  SpaceAnalyticsAlerts,
  SpaceAnalyticsOverview,
  SpaceAnalyticsParticipants,
  SpaceAnalyticsPeople,
  SpaceAnalyticsPublishers,
  SpaceLearningFilters,
  SpaceLearningOutline,
  SpaceLearningQuestions,
  SpaceLearningQueues,
  SpaceMyPublishedTasks,
  SpaceSubmissionQueue,
  SpaceTaskAnalytics,
} from '@/types/apiSpaces'

import { axiosNewApi, axiosRequest } from './axiosHttp'

export const spaceAnalytics = {
  getAnalyticsOverview: (
    spaceId: number,
    params?: Partial<{
      from: number
      to: number
      categoryId: number
      publisherId: number
      taskApproved: AnalyticsApproveType
      groupBy: AnalyticsGroupBy
    }>
  ) =>
    axiosRequest<SpaceAnalyticsOverview>({ url: `/spaces/${spaceId}/analytics/overview`, method: 'GET', params }, true),
  getAnalyticsAlerts: (spaceId: number) =>
    axiosRequest<SpaceAnalyticsAlerts>({ url: `/spaces/${spaceId}/analytics/alerts`, method: 'GET' }, true),
  getAnalyticsPublishers: (
    spaceId: number,
    params?: Partial<{
      from: number
      to: number
      categoryId: number
      taskApproved: AnalyticsApproveType
      sortBy: 'taskCount' | 'participantCount' | 'successRate' | 'lastTaskCreatedAt'
      sortOrder: AnalyticsSortOrder
    }>
  ) =>
    axiosRequest<SpaceAnalyticsPublishers>(
      { url: `/spaces/${spaceId}/analytics/publishers`, method: 'GET', params },
      true
    ),
  getAnalyticsTasks: (
    spaceId: number,
    params?: Partial<{
      from: number
      to: number
      categoryId: number
      publisherId: number
      taskApproved: AnalyticsApproveType
      hasPendingReview: boolean
      hasPendingApproval: boolean
      sortBy: 'createdAt' | 'participantCount' | 'successRate' | 'pendingReviewCount'
      sortOrder: AnalyticsSortOrder
    }>
  ) => axiosRequest<SpaceTaskAnalytics>({ url: `/spaces/${spaceId}/analytics/tasks`, method: 'GET', params }, true),
  getAnalyticsPeople: (spaceId: number) =>
    axiosRequest<SpaceAnalyticsPeople>({ url: `/spaces/${spaceId}/analytics/people`, method: 'GET' }, true),
  getAnalyticsParticipants: (
    spaceId: number,
    params?: Partial<{
      from: number
      to: number
      categoryId: number
      publisherId: number
      taskApproved: AnalyticsApproveType
      participationApproved: AnalyticsApproveType
      completionStatus: AnalyticsCompletionType
      realName: AnalyticsRealNameType
      groupBy: AnalyticsGroupBy
    }>
  ) =>
    axiosRequest<SpaceAnalyticsParticipants>(
      { url: `/spaces/${spaceId}/analytics/participants`, method: 'GET', params },
      true
    ),
  getLearningFilters: (spaceId: number) =>
    axiosRequest<SpaceLearningFilters>({ url: `/spaces/${spaceId}/analytics/learning/filters`, method: 'GET' }, true),
  getLearningQuestions: (spaceId: number, params?: Partial<{ from: number; to: number; knowledgePoint: number }>) =>
    axiosRequest<SpaceLearningQuestions>(
      { url: `/spaces/${spaceId}/analytics/learning/questions`, method: 'GET', params },
      true
    ),
  getLearningQueues: (spaceId: number, params?: Partial<{ from: number; to: number }>) =>
    axiosRequest<SpaceLearningQueues>(
      { url: `/spaces/${spaceId}/analytics/learning/queues`, method: 'GET', params },
      true
    ),
  buildLearningOutline: (spaceId: number, data: { blockIds: string[] }) =>
    axiosRequest<SpaceLearningOutline>(
      { url: `/spaces/${spaceId}/analytics/learning/outline`, method: 'POST', data },
      true
    ),
  // ---- 学情分析 · 全班整体（§ learning analysis, step 3）-------------------------------
  // `app/api/routes/spaces_learning_class.py`，门是 `view_analytics`。只有整体，例句只标代号。
  getLearningClassOverview: (spaceId: number) =>
    axiosRequest<LearningClassOverview>(
      { url: `/spaces/${spaceId}/learning-analysis/class/overview`, method: 'GET' },
      true
    ),
  /** 两条分界（问到的人占成员的比例、涉及的人里卡住的比例）由页面调，不存。 */
  getLearningClassPoints: (spaceId: number, params?: Partial<{ askedRatio: number; stuckRatio: number }>) =>
    axiosRequest<LearningClassPoints>(
      { url: `/spaces/${spaceId}/learning-analysis/class/points`, method: 'GET', params },
      true
    ),
  getLearningClassPoint: (spaceId: number, pointId: number) =>
    axiosRequest<LearningClassPointDetail>(
      { url: `/spaces/${spaceId}/learning-analysis/class/points/${pointId}`, method: 'GET' },
      true
    ),
  /** 推送巩固题给卡在这个知识点上的成员：系统自己找人，只回人数（第 4 步）。 */
  pushLearningPractice: (spaceId: number, pointId: number) =>
    axiosRequest<LearningPushResult>(
      { url: `/spaces/${spaceId}/learning-analysis/class/points/${pointId}/push`, method: 'POST' },
      true
    ),
  getLearningClassQuestions: (spaceId: number) =>
    axiosRequest<LearningClassQuestions>(
      { url: `/spaces/${spaceId}/learning-analysis/class/questions`, method: 'GET' },
      true
    ),
  /** 一簇问题或一种误解的匿名对话摘录，二选一。 */
  getLearningClassExcerpts: (spaceId: number, params: { cluster: number } | { misconception: number }) =>
    axiosRequest<LearningClassExcerpts>(
      { url: `/spaces/${spaceId}/learning-analysis/class/excerpts`, method: 'GET', params },
      true
    ),
  getLearningClassWords: (spaceId: number) =>
    axiosRequest<LearningClassWords>({ url: `/spaces/${spaceId}/learning-analysis/class/words`, method: 'GET' }, true),
  getLearningClassTasks: (spaceId: number) =>
    axiosRequest<LearningClassTasks>({ url: `/spaces/${spaceId}/learning-analysis/class/tasks`, method: 'GET' }, true),
  getLearningClassBehavior: (spaceId: number) =>
    axiosRequest<LearningClassBehavior>(
      { url: `/spaces/${spaceId}/learning-analysis/class/behavior`, method: 'GET' },
      true
    ),
  getLearningClassTrend: (spaceId: number) =>
    axiosRequest<LearningClassTrend>({ url: `/spaces/${spaceId}/learning-analysis/class/trend`, method: 'GET' }, true),
  /** 导出报告：返回文件本身（PDF / Excel / Word）。服务端记下谁、什么时候导出的。 */
  exportLearningClassReport: async (spaceId: number, data: LearningExportRequest): Promise<Blob> =>
    (
      await axiosNewApi.request<Blob>({
        url: `/spaces/${spaceId}/learning-analysis/class/export`,
        method: 'POST',
        data,
        responseType: 'blob',
      })
    ).data,
  getMyPublishedTasks: (
    spaceId: number,
    params?: Partial<{
      from: number
      to: number
      categoryId: number
      approved: AnalyticsApproveType
      hasPendingParticipantApproval: boolean
      hasPendingReview: boolean
      sortBy: 'createdAt' | 'publishedAt' | 'participantCount' | 'pendingReviewCount' | 'successRate'
      sortOrder: AnalyticsSortOrder
    }>
  ) =>
    axiosRequest<SpaceMyPublishedTasks>({ url: `/spaces/${spaceId}/me/publishing/tasks`, method: 'GET', params }, true),
  getSubmissionQueue: (
    spaceId: number,
    params: { reviewed?: boolean; taskId?: number; pageStart?: number; pageSize?: number } = {}
  ) => axiosRequest<SpaceSubmissionQueue>({ url: `/spaces/${spaceId}/submissions`, method: 'GET', params }, true),
}
