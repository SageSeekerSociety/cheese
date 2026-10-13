import type { NewAttitudeType } from '@/constants'
import type { AttitudeStats, Page, Question, QuestionInvitation, User } from '@/types'

import { axiosMutation, axiosRequest } from './axiosHttp'

/** Questions ride the primary (legacy) API origin; JSON calls expose only the
 *  payload, and commands with no payload return an explicit success sentinel. */
export namespace QuestionApi {
  export type AskQuestionRequest = {
    title: string
    content: string
    type: number
    topics: number[]
    group_id?: number
    bounty: number
  }

  export type AskQuestionResponse = { id: number }
  export type QuestionDetailResponse = { question: Question }
  export type FollowQuestionResponse = { follow_count: number }
  export type AttitudeQuestionResponse = { attitudes: AttitudeStats }
  export type QuestionInvitationRecommendResponse = { users: User[] }
  export type QuestionInviteUserResponse = { invitation_id: number }
  export type GetInvitationsResponse = { invitations: QuestionInvitation[]; page: Page }

  export const ask = (data: AskQuestionRequest) =>
    axiosRequest<AskQuestionResponse>({ url: '/questions', method: 'POST', data })

  export const detail = (id: number) => axiosRequest<QuestionDetailResponse>({ url: `/questions/${id}`, method: 'GET' })

  export const attitudeQuestion = (id: number, attitudeType: NewAttitudeType) =>
    axiosRequest<AttitudeQuestionResponse>({
      url: `/questions/${id}/attitudes`,
      method: 'POST',
      data: { attitude_type: attitudeType },
    })

  export const invitationRecommend = (id: number) =>
    axiosRequest<QuestionInvitationRecommendResponse>({
      url: `/questions/${id}/invitations/recommendations`,
      method: 'GET',
    })

  export const followQuestion = (id: number) =>
    axiosRequest<FollowQuestionResponse>({ url: `/questions/${id}/followers`, method: 'POST' })

  export const unfollowQuestion = (id: number) =>
    axiosRequest<FollowQuestionResponse>({ url: `/questions/${id}/followers`, method: 'DELETE' })

  export const inviteUser = (questionId: number, userId: number) =>
    axiosRequest<QuestionInviteUserResponse>({
      url: `/questions/${questionId}/invitations`,
      method: 'POST',
      data: { user_id: userId },
    })

  export const cancelInviteUser = (questionId: number, invitationId: number) =>
    axiosMutation({ url: `/questions/${questionId}/invitations/${invitationId}`, method: 'DELETE' })

  // 邀请名单分页。旧模块把 pageStart 明写在 params 里（值为 undefined 时 axios 会省略该键），
  // pageSize 有默认 20——两边保持一致。
  export const getInvitaions = (questionId: number, pageStart?: number, pageSize = 20) =>
    axiosRequest<GetInvitationsResponse>({
      url: `/questions/${questionId}/invitations`,
      method: 'GET',
      params: { pageStart, pageSize },
    })

  export const addBounty = (questionId: number, bounty: number) =>
    axiosMutation({ url: `/questions/${questionId}/bounty`, method: 'PUT', data: { bounty } })

  export const acceptAnswer = (questionId: number, answerId: number) =>
    axiosMutation({ url: `/questions/${questionId}/acceptance?answer_id=${answerId}`, method: 'PUT' })
}
