import type {
  DomainGroup,
  Space,
  SpaceAnnouncement,
  SpaceCategory,
  SpaceInviteCode,
  SpaceMaterial,
  SpaceMaterialVisibility,
  SpaceMember,
  Topic,
} from '@/types'
import type {
  AnalyticsApproveType,
  AnalyticsCompletionType,
  AnalyticsGroupBy,
  AnalyticsRealNameType,
  AnalyticsSortOrder,
  GetSpacesResponseData,
  PatchSpaceAdminRequestData,
  PatchSpaceAnnouncementRequestData,
  PatchSpaceCategoryRequestData,
  PatchSpaceDomainGroupRequestData,
  PatchSpaceInviteCodeRequestData,
  PatchSpaceRequestData,
  PostSpaceAdminRequestData,
  PostSpaceAnnouncementRequestData,
  PostSpaceCategoryRequestData,
  PostSpaceDomainGroupRequestData,
  PostSpaceInviteCodeRequestData,
  PostSpaceJoinRequestData,
  PostSpaceMemberRequestData,
  PostSpaceRequestData,
  SpaceAnalyticsAlerts,
  SpaceAnalyticsOverview,
  SpaceAnalyticsParticipants,
  SpaceAnalyticsPeople,
  SpaceAnalyticsPublishers,
  SpaceAnnouncementList,
  SpaceLearningFilters,
  SpaceLearningOutline,
  SpaceLearningQuestions,
  SpaceLearningQueues,
  SpaceMyPublishedTasks,
  SpaceSubmissionQueue,
  SpaceTaskAnalytics,
} from './types'

import { AxiosProgressEvent } from 'axios'

import { NewApiInstance } from '../index'

export namespace SpacesApi {
  export const applications = (offset = 0) =>
    NewApiInstance.request<{ items: import('./types').SpaceApplication[] }>({
      url: '/space-applications',
      method: 'GET',
      params: { offset, limit: 50 },
    })
  export const resubmit = (id: number, data: PostSpaceRequestData) =>
    NewApiInstance.request({
      url: `/space-applications/${id}/resubmit`,
      method: 'POST',
      data,
    })
  // `limit` 是加出来的第三个参数（原先是写死的 50）：这条路由既不给总数也不给
  // `has_more`，调用方想知道「后面还有没有」只能多要一条 —— 要 51 条，回来 51 条
  // 就说明还有。默认值不变，老的调用点行为一字不差。
  export const reviews = (status: string, offset = 0, limit = 50) =>
    NewApiInstance.request<{ items: import('./types').SpaceApplication[] }>({
      url: '/admin/spaces',
      method: 'GET',
      params: { status, offset, limit },
    })
  export const review = (id: number, approved: boolean, reason = '') =>
    NewApiInstance.request({
      url: `/admin/spaces/${id}/review`,
      method: 'POST',
      data: { approved, reason },
    })

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

  export const listAnnouncements = (spaceId: number) =>
    NewApiInstance.request<SpaceAnnouncementList>({
      url: `/spaces/${spaceId}/announcements`,
      method: 'GET',
    })

  export const publishAnnouncement = (spaceId: number, data: PostSpaceAnnouncementRequestData) =>
    NewApiInstance.request<{ announcement: SpaceAnnouncement }>({
      url: `/spaces/${spaceId}/announcements`,
      method: 'POST',
      data,
    })

  export const updateAnnouncement = (
    spaceId: number,
    announcementId: number,
    data: PatchSpaceAnnouncementRequestData
  ) =>
    NewApiInstance.request<{ announcement: SpaceAnnouncement }>({
      url: `/spaces/${spaceId}/announcements/${announcementId}`,
      method: 'PATCH',
      data,
    })

  export const deleteAnnouncement = (spaceId: number, announcementId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/announcements/${announcementId}`,
      method: 'DELETE',
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

  export const updateInviteCode = (spaceId: number, codeId: number, data: PatchSpaceInviteCodeRequestData) =>
    NewApiInstance.request<{ inviteCode: SpaceInviteCode }>({
      url: `/spaces/${spaceId}/invite-codes/${codeId}`,
      method: 'PATCH',
      data,
    })

  export const revokeInviteCode = (spaceId: number, codeId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/invite-codes/${codeId}`,
      method: 'DELETE',
    })

  /**
   * 这块板上的资料。成员看到「所有成员」那一档，管理员两档都看到；清单里
   * **没有 `url`** —— 要字节走 `downloadMaterial`。
   *
   * `canManage` 由服务端给：能不能传、改档、撤下来是同一批人（板子的管理员），
   * 界面拿它决定摆不摆那几个入口，不自己猜。
   */
  export const listMaterials = (spaceId: number) =>
    NewApiInstance.request<{ materials: SpaceMaterial[]; canManage: boolean }>({
      url: `/spaces/${spaceId}/materials`,
      method: 'GET',
    })

  export const uploadMaterial = (
    spaceId: number,
    file: File,
    visibility: SpaceMaterialVisibility,
    onProgress?: (progressEvent: AxiosProgressEvent) => void
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    // 定档和上传是同一步：先传上去再补档，中间那段时间这份文件是按默认档
    // 露在外面的。
    formData.append('visibility', visibility)
    return NewApiInstance.request<{ material: SpaceMaterial }>({
      url: `/spaces/${spaceId}/materials`,
      method: 'POST',
      data: formData,
      timeout: 60000,
      onUploadProgress: onProgress,
    })
  }

  export const updateMaterialVisibility = (spaceId: number, materialId: number, visibility: SpaceMaterialVisibility) =>
    NewApiInstance.request<{ material: SpaceMaterial }>({
      url: `/spaces/${spaceId}/materials/${materialId}`,
      method: 'PATCH',
      data: { visibility },
    })

  export const deleteMaterial = (spaceId: number, materialId: number) =>
    NewApiInstance.request({
      url: `/spaces/${spaceId}/materials/${materialId}`,
      method: 'DELETE',
    })

  /**
   * 素材的字节。`responseType: 'blob'` 时 `Api.request` 交回的就是那个 Blob
   * 本身 —— 它只把认得出是 axios 响应的那些拆一层 `data`，而 Blob 没有
   * `status` 那一格 —— 所以这里按实际形状收窄类型。
   */
  export const downloadMaterial = (spaceId: number, materialId: number) =>
    NewApiInstance.request<Blob>({
      url: `/spaces/${spaceId}/materials/${materialId}/download`,
      method: 'GET',
      responseType: 'blob',
    }) as unknown as Promise<Blob>

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

  // 逐人那一格 + 「领了没动」的名单。与其它整板分析接口同一个门：非管理员 403。
  export const getAnalyticsPeople = (spaceId: number) =>
    NewApiInstance.request<SpaceAnalyticsPeople>({
      url: `/spaces/${spaceId}/analytics/people`,
      method: 'GET',
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

  // 学习维度。筛选那一维的 knowledgePoint 是**分类 id**（知识点今天就是课程分类），
  // 返回的每条发言里的 knowledgePoint 是**分类名**，两者不要混用。
  export const getLearningFilters = (spaceId: number) =>
    NewApiInstance.request<SpaceLearningFilters>({
      url: `/spaces/${spaceId}/analytics/learning/filters`,
      method: 'GET',
    })

  export const getLearningQuestions = (
    spaceId: number,
    params?: Partial<{
      from: number
      to: number
      student: string
      knowledgePoint: number
    }>
  ) =>
    NewApiInstance.request<SpaceLearningQuestions>({
      url: `/spaces/${spaceId}/analytics/learning/questions`,
      method: 'GET',
      params,
    })

  export const getLearningQueues = (
    spaceId: number,
    params?: Partial<{
      from: number
      to: number
      student: string
    }>
  ) =>
    NewApiInstance.request<SpaceLearningQueues>({
      url: `/spaces/${spaceId}/analytics/learning/queues`,
      method: 'GET',
      params,
    })

  // POST 而不是 GET: 勾的是哪几条会随人一直变，而且可能几十个 id。
  export const buildLearningOutline = (spaceId: number, data: { blockIds: string[] }) =>
    NewApiInstance.request<SpaceLearningOutline>({
      url: `/spaces/${spaceId}/analytics/learning/outline`,
      method: 'POST',
      data,
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

  /**
   * 一整门课的作业与验收（管理员版面）。
   *
   * `reviewed: false` 就是验收队列；不给就是全部。每行是「谁的哪份作业」，
   * 带 `participantId` 供既有的提交 / 评审接口使用。
   */
  export const getSubmissionQueue = (
    spaceId: number,
    params: {
      reviewed?: boolean
      taskId?: number
      pageStart?: number
      pageSize?: number
    } = {}
  ) =>
    NewApiInstance.request<SpaceSubmissionQueue>({
      url: `/spaces/${spaceId}/submissions`,
      method: 'GET',
      params,
    })
}
