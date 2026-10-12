import type {
  AnalyticsApproveType,
  AnalyticsCompletionType,
  AnalyticsGroupBy,
  AnalyticsRealNameType,
  AnalyticsSortOrder,
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

import { axiosRequest } from './axiosHttp'

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
  getLearningQuestions: (
    spaceId: number,
    params?: Partial<{ from: number; to: number; student: string; knowledgePoint: number }>
  ) =>
    axiosRequest<SpaceLearningQuestions>(
      { url: `/spaces/${spaceId}/analytics/learning/questions`, method: 'GET', params },
      true
    ),
  getLearningQueues: (spaceId: number, params?: Partial<{ from: number; to: number; student: string }>) =>
    axiosRequest<SpaceLearningQueues>(
      { url: `/spaces/${spaceId}/analytics/learning/queues`, method: 'GET', params },
      true
    ),
  buildLearningOutline: (spaceId: number, data: { blockIds: string[] }) =>
    axiosRequest<SpaceLearningOutline>(
      { url: `/spaces/${spaceId}/analytics/learning/outline`, method: 'POST', data },
      true
    ),
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
