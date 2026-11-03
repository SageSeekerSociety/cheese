import type { AxiosProgressEvent } from 'axios'
import type {
  DomainGroup,
  Space,
  SpaceAnnouncement,
  SpaceCategory,
  SpaceInvite,
  SpaceJoinLink,
  SpaceJoinRequest,
  SpaceMaterial,
  SpaceMaterialVisibility,
  SpaceMember,
  Topic,
} from '@/types'
import type {
  GetSpacesResponseData,
  PatchSpaceAdminRequestData,
  PatchSpaceAnnouncementRequestData,
  PatchSpaceCategoryRequestData,
  PatchSpaceDomainGroupRequestData,
  PatchSpaceRequestData,
  PostSpaceAdminRequestData,
  PostSpaceAnnouncementRequestData,
  PostSpaceCategoryRequestData,
  PostSpaceDomainGroupRequestData,
  PostSpaceMemberRequestData,
  PostSpaceRequestData,
  PutSpaceAccessRequestData,
  SpaceAnnouncementList,
  SpaceApplication,
} from '@/types/apiSpaces'

import { axiosMutation, axiosNewApi, axiosRequest } from './axiosHttp'
import { spaceAnalytics } from './spaceAnalytics'
import { changesSpaces } from './spacesChanged'

/** Spaces have always used the new API origin; JSON calls expose only the payload. */
export const SpacesApi = {
  ...spaceAnalytics,
  applications: (offset = 0) =>
    axiosRequest<{ items: SpaceApplication[] }>(
      { url: '/space-applications', method: 'GET', params: { offset, limit: 50 } },
      true
    ),
  resubmit: (id: number, data: PostSpaceRequestData) =>
    changesSpaces(
      axiosRequest<{ application: SpaceApplication }>(
        { url: `/space-applications/${id}/resubmit`, method: 'POST', data },
        true
      )
    ),
  reviews: (status: string, offset = 0, limit = 50) =>
    axiosRequest<{ items: SpaceApplication[] }>(
      { url: '/admin/spaces', method: 'GET', params: { status, offset, limit } },
      true
    ),
  /** `openScope`：审开放申请时带上页面显示的范围；申请在那之后被改过，服务端回 409。 */
  review: (id: number, approved: boolean, reason = '', openScope?: string[]) =>
    axiosRequest<{ application: SpaceApplication }>(
      { url: `/admin/spaces/${id}/review`, method: 'POST', data: { approved, reason, openScope } },
      true
    ),
  create: (data: PostSpaceRequestData) =>
    changesSpaces(axiosRequest<{ space: Space }>({ url: '/spaces', method: 'POST', data }, true)),
  listMembers: (spaceId: number) =>
    axiosRequest<{ members: SpaceMember[] }>({ url: `/spaces/${spaceId}/members`, method: 'GET' }, true),
  addMember: (spaceId: number, data: PostSpaceMemberRequestData) =>
    axiosRequest<{ member: SpaceMember }>({ url: `/spaces/${spaceId}/members`, method: 'POST', data }, true),
  removeMember: (spaceId: number, userId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/members/${userId}`, method: 'DELETE' }, true),
  leave: (spaceId: number) => changesSpaces(axiosMutation({ url: `/spaces/${spaceId}/leave`, method: 'POST' }, true)),
  listAnnouncements: (spaceId: number) =>
    axiosRequest<SpaceAnnouncementList>({ url: `/spaces/${spaceId}/announcements`, method: 'GET' }, true),
  publishAnnouncement: (spaceId: number, data: PostSpaceAnnouncementRequestData) =>
    axiosRequest<{ announcement: SpaceAnnouncement }>(
      { url: `/spaces/${spaceId}/announcements`, method: 'POST', data },
      true
    ),
  updateAnnouncement: (spaceId: number, announcementId: number, data: PatchSpaceAnnouncementRequestData) =>
    axiosRequest<{ announcement: SpaceAnnouncement }>(
      { url: `/spaces/${spaceId}/announcements/${announcementId}`, method: 'PATCH', data },
      true
    ),
  deleteAnnouncement: (spaceId: number, announcementId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/announcements/${announcementId}`, method: 'DELETE' }, true),
  // 加入链接：每个空间一条，所有者和管理员读、重置、切换「加入需要批准」。
  getJoinLink: (spaceId: number) =>
    axiosRequest<SpaceJoinLink>({ url: `/spaces/${spaceId}/join-link`, method: 'GET' }, true),
  updateJoinLink: (spaceId: number, data: { approval: boolean }) =>
    axiosRequest<SpaceJoinLink>({ url: `/spaces/${spaceId}/join-link`, method: 'PATCH', data }, true),
  resetJoinLink: (spaceId: number) =>
    axiosRequest<SpaceJoinLink>({ url: `/spaces/${spaceId}/join-link/reset`, method: 'POST' }, true),
  listJoinRequests: (spaceId: number) =>
    axiosRequest<{ requests: SpaceJoinRequest[] }>({ url: `/spaces/${spaceId}/join-requests`, method: 'GET' }, true),
  approveJoinRequest: (spaceId: number, requestId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/join-requests/${requestId}/approve`, method: 'POST' }, true),
  rejectJoinRequest: (spaceId: number, requestId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/join-requests/${requestId}/reject`, method: 'POST' }, true),
  // 拿着链接的人那一侧：先看是哪个空间、自己在哪一步，按下确认才加入（或申请）。
  // 令牌进路径前编码：它来自地址栏，不能让一个斜杠改了请求的是哪条路由。
  detailByJoinLink: (token: string) =>
    axiosRequest<SpaceInvite>({ url: `/space-invites/${encodeURIComponent(token)}`, method: 'GET' }, true),
  joinByJoinLink: (token: string) =>
    changesSpaces(
      axiosRequest<SpaceInvite>({ url: `/space-invites/${encodeURIComponent(token)}/join`, method: 'POST' }, true)
    ),
  listMaterials: (spaceId: number) =>
    axiosRequest<{ materials: SpaceMaterial[]; canManage: boolean }>(
      { url: `/spaces/${spaceId}/materials`, method: 'GET' },
      true
    ),
  uploadMaterial: (
    spaceId: number,
    file: File,
    visibility: SpaceMaterialVisibility,
    onProgress?: (event: AxiosProgressEvent) => void
  ) => {
    const data = new FormData()
    data.append('file', file)
    data.append('visibility', visibility)
    return axiosRequest<{ material: SpaceMaterial }>(
      { url: `/spaces/${spaceId}/materials`, method: 'POST', data, timeout: 60000, onUploadProgress: onProgress },
      true
    )
  },
  updateMaterialVisibility: (spaceId: number, materialId: number, visibility: SpaceMaterialVisibility) =>
    axiosRequest<{ material: SpaceMaterial }>(
      { url: `/spaces/${spaceId}/materials/${materialId}`, method: 'PATCH', data: { visibility } },
      true
    ),
  deleteMaterial: (spaceId: number, materialId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/materials/${materialId}`, method: 'DELETE' }, true),
  /** `preview`：页面上点开看一眼，不记一次下载。 */
  downloadMaterial: async (spaceId: number, materialId: number, preview = false): Promise<Blob> =>
    (
      await axiosNewApi.request<Blob>({
        url: `/spaces/${spaceId}/materials/${materialId}/download`,
        method: 'GET',
        params: preview ? { preview: true } : undefined,
        responseType: 'blob',
      })
    ).data,
  update: (spaceId: number, data: PatchSpaceRequestData) =>
    axiosRequest<{ space: Space }>({ url: `/spaces/${spaceId}`, method: 'PATCH', data }, true),
  /** 只对成员开放，或开放给一组邮箱后缀。扩大范围要等平台审核，回来的空间带着那条申请。 */
  setAccess: (spaceId: number, data: PutSpaceAccessRequestData) =>
    axiosRequest<{ space: Space }>({ url: `/spaces/${spaceId}/access`, method: 'PUT', data }, true),
  del: (spaceId: number) => changesSpaces(axiosMutation({ url: `/spaces/${spaceId}`, method: 'DELETE' }, true)),
  detail: (spaceId: number, params: { queryClassificationTopics?: boolean; queryMyRank?: boolean } = {}) =>
    axiosRequest<{ space: Space }>({ url: `/spaces/${spaceId}`, method: 'GET', params }, true),
  list: (params: { pageSize?: number; pageStart?: number; sort_by: string; sort_order: string }) =>
    axiosRequest<GetSpacesResponseData>({ url: '/spaces', method: 'GET', params }, true),
  addAdmin: (spaceId: number, data: PostSpaceAdminRequestData) =>
    axiosRequest<{ space: Space }>({ url: `/spaces/${spaceId}/managers`, method: 'POST', data }, true),
  updateAdmin: (spaceId: number, userId: number, data: PatchSpaceAdminRequestData) =>
    axiosRequest<{ space: Space }>({ url: `/spaces/${spaceId}/managers/${userId}`, method: 'PATCH', data }, true),
  removeAdmin: (spaceId: number, userId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/managers/${userId}`, method: 'DELETE' }, true),
  listCategories: (spaceId: number, params: { includeArchived?: boolean } = {}) =>
    axiosRequest<{ categories: SpaceCategory[] }>(
      { url: `/spaces/${spaceId}/categories`, method: 'GET', params },
      true
    ),
  createCategory: (spaceId: number, data: PostSpaceCategoryRequestData) =>
    axiosRequest<{ category: SpaceCategory }>({ url: `/spaces/${spaceId}/categories`, method: 'POST', data }, true),
  getCategory: (spaceId: number, categoryId: number) =>
    axiosRequest<{ category: SpaceCategory }>(
      { url: `/spaces/${spaceId}/categories/${categoryId}`, method: 'GET' },
      true
    ),
  updateCategory: (spaceId: number, categoryId: number, data: PatchSpaceCategoryRequestData) =>
    axiosRequest<{ category: SpaceCategory }>(
      { url: `/spaces/${spaceId}/categories/${categoryId}`, method: 'PATCH', data },
      true
    ),
  deleteCategory: (spaceId: number, categoryId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/categories/${categoryId}`, method: 'DELETE' }, true),
  archiveCategory: (spaceId: number, categoryId: number) =>
    axiosRequest<{ category: SpaceCategory }>(
      { url: `/spaces/${spaceId}/categories/${categoryId}/archive`, method: 'POST' },
      true
    ),
  unarchiveCategory: (spaceId: number, categoryId: number) =>
    axiosRequest<{ category: SpaceCategory }>(
      { url: `/spaces/${spaceId}/categories/${categoryId}/archive`, method: 'DELETE' },
      true
    ),
  getSpaceTopics: (spaceId: number, limit = 10, sort?: 'popularity' | 'name', keyword?: string) =>
    axiosRequest<{ topics: Topic[] }>(
      { url: `/spaces/${spaceId}/topics`, method: 'GET', params: { limit, sort, keyword } },
      true
    ),
  listDomainGroups: (spaceId: number) =>
    axiosRequest<{ groups: DomainGroup[] }>({ url: `/spaces/${spaceId}/domain-groups`, method: 'GET' }, true),
  createDomainGroup: (spaceId: number, data: PostSpaceDomainGroupRequestData) =>
    axiosRequest<{ group: DomainGroup }>({ url: `/spaces/${spaceId}/domain-groups`, method: 'POST', data }, true),
  updateDomainGroup: (spaceId: number, groupId: number, data: PatchSpaceDomainGroupRequestData) =>
    axiosRequest<{ group: DomainGroup }>(
      { url: `/spaces/${spaceId}/domain-groups/${groupId}`, method: 'PATCH', data },
      true
    ),
  deleteDomainGroup: (spaceId: number, groupId: number) =>
    axiosMutation({ url: `/spaces/${spaceId}/domain-groups/${groupId}`, method: 'DELETE' }, true),
}
