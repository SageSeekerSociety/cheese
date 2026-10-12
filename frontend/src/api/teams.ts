import type {
  ApplicationStatus,
  Page,
  Team,
  TeamInvitationCreate,
  TeamJoinRequestCreate,
  TeamMember,
  TeamMembershipApplication,
  TeamVisibility,
} from '@/types'

import { axiosMutation, axiosRequest } from './axiosHttp'

/** Teams have always used the new API origin; JSON calls expose only the payload.
 *  A handle or join-link token goes on the path URL-encoded, and a member add
 *  keeps the wire field `user_id` (not `userId`) the server expects. */
export namespace TeamsApi {
  export type PostTeamRequestData = {
    name: string
    // Blank lets the server name it `team-<id>` until the owner picks one.
    handle?: string
    description: string
    intro: string
    avatarId: number
  }

  export type PatchTeamRequestData = {
    name?: string
    intro?: string
    avatarId?: number
    visibility?: TeamVisibility
    handle?: string
  }

  export type PostTeamMemberRequestData = {
    user_id: number
    role: 'OWNER' | 'ADMIN' | 'MEMBER'
  }

  export type PatchTeamMemberRequestData = {
    role: 'OWNER' | 'ADMIN' | 'MEMBER'
  }

  export type SearchTeamsRequestData = {
    query: string
    pageStart?: number
    pageSize?: number
  }

  export const search = (params: SearchTeamsRequestData) =>
    axiosRequest<{ teams: Team[]; page: Page }>({ url: '/teams', method: 'GET', params }, true)

  export const create = (data: PostTeamRequestData) =>
    axiosRequest<{ team: Team }>({ url: '/teams', method: 'POST', data }, true)

  export const update = (teamId: number, data: PatchTeamRequestData) =>
    axiosRequest<{ team: Team }>({ url: `/teams/${teamId}`, method: 'PATCH', data }, true)

  export const del = (teamId: number) => axiosMutation({ url: `/teams/${teamId}`, method: 'DELETE' }, true)

  export const detail = (teamId: number) =>
    axiosRequest<{ team: Team }>({ url: `/teams/${teamId}`, method: 'GET' }, true)

  // 小队链接：长期有效，重置后旧链接失效。approval 开着，拿到链接只能申请，要 owner/admin
  // 批准；关着就直接加入。
  export type TeamJoinLink = { token: string; approval: boolean }

  export const getJoinLink = (teamId: number) =>
    axiosRequest<TeamJoinLink>({ url: `/teams/${teamId}/join-link`, method: 'GET' }, true)

  export const updateJoinLink = (teamId: number, data: { approval: boolean }) =>
    axiosRequest<TeamJoinLink>({ url: `/teams/${teamId}/join-link`, method: 'PATCH', data }, true)

  export const resetJoinLink = (teamId: number) =>
    axiosRequest<TeamJoinLink>({ url: `/teams/${teamId}/join-link/reset`, method: 'POST' }, true)

  export const detailByJoinLink = (token: string) =>
    axiosRequest<{ team: Team }>({ url: `/team-invites/${encodeURIComponent(token)}`, method: 'GET' }, true)

  // 加入（或在需要审批时提交申请）；返回的 team.joinStatus 说明结果是 member 还是 pending。
  export const joinByJoinLink = (token: string, data?: TeamJoinRequestCreate) =>
    axiosRequest<{ team: Team }>(
      { url: `/team-invites/${encodeURIComponent(token)}/join`, method: 'POST', data },
      true
    )

  export const join = (teamId: number, data?: TeamJoinRequestCreate) =>
    axiosRequest<{ team: Team }>({ url: `/teams/${teamId}/join`, method: 'POST', data }, true)

  export const detailByHandle = (handle: string) =>
    axiosRequest<{ team: Team }>({ url: `/teams/by-handle/${encodeURIComponent(handle)}`, method: 'GET' }, true)

  export const getMyTeams = () =>
    axiosRequest<{ teams: Team[] }>({ url: '/teams/my-teams', method: 'GET' }, true)

  export const list = (params: { pageSize?: number; pageStart?: number; sortBy: string; sortOrder: string }) =>
    axiosRequest<{ teams: Team[]; page: Page }>({ url: '/teams', method: 'GET', params }, true)

  export const addMember = (teamId: number, data: PostTeamMemberRequestData) =>
    axiosRequest<{ team: Team }>({ url: `/teams/${teamId}/members`, method: 'POST', data }, true)

  export const updateMember = (teamId: number, userId: number, data: PatchTeamMemberRequestData) =>
    axiosRequest<{ team: Team }>({ url: `/teams/${teamId}/members/${userId}`, method: 'PATCH', data }, true)

  export const removeMember = (teamId: number, userId: number) =>
    axiosMutation({ url: `/teams/${teamId}/members/${userId}`, method: 'DELETE' }, true)

  /** 把团队交给另一位成员：他成为所有者，我降为管理员。 */
  export const transferOwner = (teamId: number, userId: number) =>
    axiosRequest<{ team: Team }>({ url: `/teams/${teamId}/owner`, method: 'PUT', data: { userId } }, true)

  export const getMembers = (teamId: number) =>
    axiosRequest<{ members: TeamMember[] }>({ url: `/teams/${teamId}/members`, method: 'GET' }, true)

  // 团队申请加入请求相关API
  export const listTeamJoinRequests = (
    teamId: number,
    params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }
  ) =>
    axiosRequest<{ applications: TeamMembershipApplication[]; page: Page }>(
      { url: `/teams/${teamId}/requests`, method: 'GET', params },
      true
    )

  export const approveJoinRequest = (teamId: number, requestId: number) =>
    axiosMutation({ url: `/teams/${teamId}/requests/${requestId}/approve`, method: 'POST' }, true)

  export const rejectJoinRequest = (teamId: number, requestId: number) =>
    axiosMutation({ url: `/teams/${teamId}/requests/${requestId}/reject`, method: 'POST' }, true)

  // 团队邀请相关API
  export const createInvitation = (teamId: number, data: TeamInvitationCreate) =>
    axiosRequest<{ invitation: TeamMembershipApplication }>(
      { url: `/teams/${teamId}/invitations`, method: 'POST', data },
      true
    )

  export const listTeamInvitations = (
    teamId: number,
    params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }
  ) =>
    axiosRequest<{ invitations: TeamMembershipApplication[]; page: Page }>(
      { url: `/teams/${teamId}/invitations`, method: 'GET', params },
      true
    )

  export const cancelInvitation = (teamId: number, invitationId: number) =>
    axiosMutation({ url: `/teams/${teamId}/invitations/${invitationId}`, method: 'DELETE' }, true)

  // 用户视角的请求和邀请API
  export const listMyJoinRequests = (params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }) =>
    axiosRequest<{ requests: TeamMembershipApplication[]; page: Page }>(
      { url: '/users/me/team-requests', method: 'GET', params },
      true
    )

  export const cancelMyJoinRequest = (requestId: number) =>
    axiosMutation({ url: `/users/me/team-requests/${requestId}`, method: 'DELETE' }, true)

  export const listMyInvitations = (params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }) =>
    axiosRequest<{ invitations: TeamMembershipApplication[]; page: Page }>(
      { url: '/users/me/team-invitations', method: 'GET', params },
      true
    )

  export const acceptInvitation = (invitationId: number) =>
    axiosMutation({ url: `/users/me/team-invitations/${invitationId}/accept`, method: 'POST' }, true)

  export const declineInvitation = (invitationId: number) =>
    axiosMutation({ url: `/users/me/team-invitations/${invitationId}/decline`, method: 'POST' }, true)
}
