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

import { NewApiInstance } from '../index'

export namespace TeamsApi {
  export type PostTeamRequestData = {
    name: string
    description: string
    intro: string
    avatarId: number
  }

  export type PatchTeamRequestData = {
    name?: string
    intro?: string
    avatarId?: number
    visibility?: TeamVisibility
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
    NewApiInstance.request<{ teams: Team[]; page: Page }>({
      url: '/teams',
      method: 'GET',
      params,
    })

  export const create = (data: PostTeamRequestData) =>
    NewApiInstance.request<{ team: Team }>({
      url: '/teams',
      method: 'POST',
      data,
    })

  export const update = (teamId: number, data: PatchTeamRequestData) =>
    NewApiInstance.request<{ team: Team }>({
      url: `/teams/${teamId}`,
      method: 'PATCH',
      data,
    })

  export const del = (teamId: number) =>
    NewApiInstance.request({
      url: `/teams/${teamId}`,
      method: 'DELETE',
    })

  export const detail = (teamId: number) =>
    NewApiInstance.request<{ team: Team }>({
      url: `/teams/${teamId}`,
      method: 'GET',
    })

  // 小队链接：长期有效，重置后旧链接失效。approval 开着，拿到链接只能申请，要 owner/admin
  // 批准；关着就直接加入。
  export type TeamJoinLink = { token: string; approval: boolean }

  export const getJoinLink = (teamId: number) =>
    NewApiInstance.request<TeamJoinLink>({
      url: `/teams/${teamId}/join-link`,
      method: 'GET',
    })

  export const updateJoinLink = (teamId: number, data: { approval: boolean }) =>
    NewApiInstance.request<TeamJoinLink>({
      url: `/teams/${teamId}/join-link`,
      method: 'PATCH',
      data,
    })

  export const resetJoinLink = (teamId: number) =>
    NewApiInstance.request<TeamJoinLink>({
      url: `/teams/${teamId}/join-link/reset`,
      method: 'POST',
    })

  export const detailByJoinLink = (token: string) =>
    NewApiInstance.request<{ team: Team }>({
      url: `/team-invites/${encodeURIComponent(token)}`,
      method: 'GET',
    })

  // 加入（或在需要审批时提交申请）；返回的 team.joinStatus 说明结果是 member 还是 pending。
  export const joinByJoinLink = (token: string, data?: TeamJoinRequestCreate) =>
    NewApiInstance.request<{ team: Team }>({
      url: `/team-invites/${encodeURIComponent(token)}/join`,
      method: 'POST',
      data,
    })

  export const join = (teamId: number, data?: TeamJoinRequestCreate) =>
    NewApiInstance.request<{ team: Team }>({
      url: `/teams/${teamId}/join`,
      method: 'POST',
      data,
    })

  export const getMyTeams = () =>
    NewApiInstance.request<{ teams: Team[] }>({
      url: '/teams/my-teams',
      method: 'GET',
    })

  export const list = (params: { pageSize?: number; pageStart?: number; sortBy: string; sortOrder: string }) =>
    NewApiInstance.request<{ teams: Team[]; page: Page }>({
      url: '/teams',
      method: 'GET',
      params,
    })

  export const addMember = (teamId: number, data: PostTeamMemberRequestData) =>
    NewApiInstance.request<{ team: Team }>({
      url: `/teams/${teamId}/members`,
      method: 'POST',
      data,
    })

  export const updateMember = (teamId: number, userId: number, data: PatchTeamMemberRequestData) =>
    NewApiInstance.request<{ team: Team }>({
      url: `/teams/${teamId}/members/${userId}`,
      method: 'PATCH',
      data,
    })

  export const removeMember = (teamId: number, userId: number) =>
    NewApiInstance.request({
      url: `/teams/${teamId}/members/${userId}`,
      method: 'DELETE',
    })

  export const getMembers = (teamId: number) =>
    NewApiInstance.request<{ members: TeamMember[] }>({
      url: `/teams/${teamId}/members`,
      method: 'GET',
    })

  // 团队申请加入请求相关API
  export const listTeamJoinRequests = (
    teamId: number,
    params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }
  ) =>
    NewApiInstance.request<{ applications: TeamMembershipApplication[]; page: Page }>({
      url: `/teams/${teamId}/requests`,
      method: 'GET',
      params,
    })

  export const approveJoinRequest = (teamId: number, requestId: number) =>
    NewApiInstance.request({
      url: `/teams/${teamId}/requests/${requestId}/approve`,
      method: 'POST',
    })

  export const rejectJoinRequest = (teamId: number, requestId: number) =>
    NewApiInstance.request({
      url: `/teams/${teamId}/requests/${requestId}/reject`,
      method: 'POST',
    })

  // 团队邀请相关API
  export const createInvitation = (teamId: number, data: TeamInvitationCreate) =>
    NewApiInstance.request<{ invitation: TeamMembershipApplication }>({
      url: `/teams/${teamId}/invitations`,
      method: 'POST',
      data,
    })

  export const listTeamInvitations = (
    teamId: number,
    params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }
  ) =>
    NewApiInstance.request<{ invitations: TeamMembershipApplication[]; page: Page }>({
      url: `/teams/${teamId}/invitations`,
      method: 'GET',
      params,
    })

  export const cancelInvitation = (teamId: number, invitationId: number) =>
    NewApiInstance.request({
      url: `/teams/${teamId}/invitations/${invitationId}`,
      method: 'DELETE',
    })

  // 用户视角的请求和邀请API
  export const listMyJoinRequests = (params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }) =>
    NewApiInstance.request<{ requests: TeamMembershipApplication[]; page: Page }>({
      url: '/users/me/team-requests',
      method: 'GET',
      params,
    })

  export const cancelMyJoinRequest = (requestId: number) =>
    NewApiInstance.request({
      url: `/users/me/team-requests/${requestId}`,
      method: 'DELETE',
    })

  export const listMyInvitations = (params?: { status?: ApplicationStatus; pageStart?: number; pageSize?: number }) =>
    NewApiInstance.request<{ invitations: TeamMembershipApplication[]; page: Page }>({
      url: '/users/me/team-invitations',
      method: 'GET',
      params,
    })

  export const acceptInvitation = (invitationId: number) =>
    NewApiInstance.request({
      url: `/users/me/team-invitations/${invitationId}/accept`,
      method: 'POST',
    })

  export const declineInvitation = (invitationId: number) =>
    NewApiInstance.request({
      url: `/users/me/team-invitations/${invitationId}/decline`,
      method: 'POST',
    })
}
