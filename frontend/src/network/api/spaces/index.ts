import type { DomainGroup, Space, SpaceCategory, SpaceInviteCode, SpaceMember, Topic } from '@/types'
import type {
  AnalyticsApproveType,
  AnalyticsCompletionType,
  AnalyticsGroupBy,
  AnalyticsRealNameType,
  AnalyticsSortOrder,
  GetSpacesResponseData,
  PatchSpaceAdminRequestData,
  PatchSpaceCategoryRequestData,
  PatchSpaceDomainGroupRequestData,
  PatchSpaceRequestData,
  PostSpaceAdminRequestData,
  PostSpaceCategoryRequestData,
  PostSpaceDomainGroupRequestData,
  PostSpaceInviteCodeRequestData,
  PostSpaceJoinRequestData,
  PostSpaceMemberRequestData,
  PostSpaceRequestData,
  SpaceAnalyticsAlerts,
  SpaceAnalyticsOverview,
  SpaceAnalyticsParticipants,
  SpaceAnalyticsPublishers,
  SpaceMyParticipatingOverview,
  SpaceMyParticipations,
  SpaceMyPublishedTasks,
  SpaceMyPublishingOverview,
  SpaceTaskAnalytics,
} from './types'

import { NewApiInstance } from '../index'

export namespace SpacesApi {
  /**
   * Creating a 凭码 space hands back the code it was born holding, so the
   * creator does not have to ask for one separately.
   */
  export const create = (data: PostSpaceRequestData) =>
    NewApiInstance.request<{ space: Space; inviteCode: SpaceInviteCode | null }>({
      url: '/spaces',
      method: 'POST',
      data,
    })

  export const join = (data: PostSpaceJoinRequestData) =>
    NewApiInstance.request<{ space: Space }>({
      url: '/spaces/join',
      method: 'POST',
      data,
    })

  export const listMembers = (spaceId: number) =>
    NewApiInstance.request<{ members: SpaceMember[] }>({
      url: `/spaces/${spaceId}/members`,
      method: 'GET',
    })

  export const addMember = (spaceId: number, data: PostSpaceMemberRequestData) =>
    NewApiInstance.request<{ member: SpaceMember }>({
      url: `/spaces/${spaceId}/members`,
      method: 'POST',
      data,
    })

  export const removeMember = (spaceId: number, userId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/members/${userId}`,
      method: 'DELETE',
    })

  export const leave = (spaceId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/leave`,
      method: 'POST',
    })

  export const listInviteCodes = (spaceId: number) =>
    NewApiInstance.request<{ inviteCodes: SpaceInviteCode[] }>({
      url: `/spaces/${spaceId}/invite-codes`,
      method: 'GET',
    })

  export const createInviteCode = (spaceId: number, data: PostSpaceInviteCodeRequestData = {}) =>
    NewApiInstance.request<{ inviteCode: SpaceInviteCode }>({
      url: `/spaces/${spaceId}/invite-codes`,
      method: 'POST',
      data,
    })

  export const update = (spaceId: number, data: PatchSpaceRequestData) =>
    NewApiInstance.request<{ space: Space }>({
      url: `/spaces/${spaceId}`,
      method: 'PATCH',
      data,
    })

  export const del = (spaceId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}`,
      method: 'DELETE',
    })

  export const detail = (
    spaceId: number,
    params: { queryClassificationTopics?: boolean; queryMyRank?: boolean } = {}
  ) =>
    NewApiInstance.request<{ space: Space }>({
      url: `/spaces/${spaceId}`,
      method: 'GET',
      params,
    })

  export const list = (params: { pageSize?: number; pageStart?: number; sort_by: string; sort_order: string }) =>
    NewApiInstance.request<GetSpacesResponseData>({
      url: '/spaces',
      method: 'GET',
      params,
    })

  export const getAnalyticsOverview = (
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
    NewApiInstance.request<SpaceAnalyticsOverview>({
      url: `/spaces/${spaceId}/analytics/overview`,
      method: 'GET',
      params,
    })

  export const getAnalyticsAlerts = (spaceId: number) =>
    NewApiInstance.request<SpaceAnalyticsAlerts>({
      url: `/spaces/${spaceId}/analytics/alerts`,
      method: 'GET',
    })

  export const getAnalyticsPublishers = (
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
    NewApiInstance.request<SpaceAnalyticsPublishers>({
      url: `/spaces/${spaceId}/analytics/publishers`,
      method: 'GET',
      params,
    })

  export const getAnalyticsTasks = (
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
  ) =>
    NewApiInstance.request<SpaceTaskAnalytics>({
      url: `/spaces/${spaceId}/analytics/tasks`,
      method: 'GET',
      params,
    })

  export const getAnalyticsParticipants = (
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
    NewApiInstance.request<SpaceAnalyticsParticipants>({
      url: `/spaces/${spaceId}/analytics/participants`,
      method: 'GET',
      params,
    })

  export const getMyPublishingOverview = (spaceId: number) =>
    NewApiInstance.request<SpaceMyPublishingOverview>({
      url: `/spaces/${spaceId}/me/publishing`,
      method: 'GET',
    })

  export const getMyPublishedTasks = (
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
    NewApiInstance.request<SpaceMyPublishedTasks>({
      url: `/spaces/${spaceId}/me/publishing/tasks`,
      method: 'GET',
      params,
    })

  export const getMyParticipatingOverview = (spaceId: number) =>
    NewApiInstance.request<SpaceMyParticipatingOverview>({
      url: `/spaces/${spaceId}/me/participating`,
      method: 'GET',
    })

  export const getMyParticipations = (
    spaceId: number,
    params?: Partial<{
      approved: AnalyticsApproveType
      completionStatus: AnalyticsCompletionType
      identityType: 'USER' | 'TEAM'
      sortBy: 'joinedAt' | 'deadline' | 'latestSubmissionAt' | 'completionStatus'
      sortOrder: AnalyticsSortOrder
    }>
  ) =>
    NewApiInstance.request<SpaceMyParticipations>({
      url: `/spaces/${spaceId}/me/participations`,
      method: 'GET',
      params,
    })

  export const addAdmin = (spaceId: number, data: PostSpaceAdminRequestData) =>
    NewApiInstance.request<{ space: Space }>({
      url: `/spaces/${spaceId}/managers`,
      method: 'POST',
      data,
    })

  export const updateAdmin = (spaceId: number, userId: number, data: PatchSpaceAdminRequestData) =>
    NewApiInstance.request<{ space: Space }>({
      url: `/spaces/${spaceId}/managers/${userId}`,
      method: 'PATCH',
      data,
    })

  export const removeAdmin = (spaceId: number, userId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/managers/${userId}`,
      method: 'DELETE',
    })

  // Categories API
  export const listCategories = (spaceId: number, params: { includeArchived?: boolean } = {}) =>
    NewApiInstance.request<{ categories: SpaceCategory[] }>({
      url: `/spaces/${spaceId}/categories`,
      method: 'GET',
      params,
    })

  export const createCategory = (spaceId: number, data: PostSpaceCategoryRequestData) =>
    NewApiInstance.request<{ category: SpaceCategory }>({
      url: `/spaces/${spaceId}/categories`,
      method: 'POST',
      data,
    })

  export const getCategory = (spaceId: number, categoryId: number) =>
    NewApiInstance.request<{ category: SpaceCategory }>({
      url: `/spaces/${spaceId}/categories/${categoryId}`,
      method: 'GET',
    })

  export const updateCategory = (spaceId: number, categoryId: number, data: PatchSpaceCategoryRequestData) =>
    NewApiInstance.request<{ category: SpaceCategory }>({
      url: `/spaces/${spaceId}/categories/${categoryId}`,
      method: 'PATCH',
      data,
    })

  export const deleteCategory = (spaceId: number, categoryId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/categories/${categoryId}`,
      method: 'DELETE',
    })

  export const archiveCategory = (spaceId: number, categoryId: number) =>
    NewApiInstance.request<{ category: SpaceCategory }>({
      url: `/spaces/${spaceId}/categories/${categoryId}/archive`,
      method: 'POST',
    })

  export const unarchiveCategory = (spaceId: number, categoryId: number) =>
    NewApiInstance.request<{ category: SpaceCategory }>({
      url: `/spaces/${spaceId}/categories/${categoryId}/archive`,
      method: 'DELETE',
    })

  export const getSpaceTopics = (spaceId: number, limit = 10, sort?: 'popularity' | 'name', keyword?: string) =>
    NewApiInstance.request<{ topics: Topic[] }>({
      url: `/spaces/${spaceId}/topics`,
      method: 'GET',
      params: { limit, sort, keyword },
    })

  // Domain Groups API
  export const listDomainGroups = (spaceId: number) =>
    NewApiInstance.request<{ groups: DomainGroup[] }>({
      url: `/spaces/${spaceId}/domain-groups`,
      method: 'GET',
    })

  export const createDomainGroup = (spaceId: number, data: PostSpaceDomainGroupRequestData) =>
    NewApiInstance.request<{ group: DomainGroup }>({
      url: `/spaces/${spaceId}/domain-groups`,
      method: 'POST',
      data,
    })

  export const updateDomainGroup = (spaceId: number, groupId: number, data: PatchSpaceDomainGroupRequestData) =>
    NewApiInstance.request<{ group: DomainGroup }>({
      url: `/spaces/${spaceId}/domain-groups/${groupId}`,
      method: 'PATCH',
      data,
    })

  export const deleteDomainGroup = (spaceId: number, groupId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/domain-groups/${groupId}`,
      method: 'DELETE',
    })
}
