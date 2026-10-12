import type { NewAttitudeType } from '@/constants'
import type { Answer, AttitudeStats, Page, Question } from '@/types'

import { axiosMutation, axiosRequest } from './axiosHttp'

/** Answers ride the primary (legacy) API origin; JSON calls expose only the payload. */
export namespace AnswersApi {
  export type GetAnswersResponse = { answers: Answer[]; page: Page }
  export type AnswerQuestionResponse = { id: number }
  export type GetAnswerDetailResponse = { question: Question; answer: Answer }
  export type AttitudeAnswerResponse = { attitudes: AttitudeStats }

  export const getAnswers = (questionId: number, pageStart?: number, pageSize = 20) =>
    axiosRequest<GetAnswersResponse>({
      url: `/questions/${questionId}/answers`,
      method: 'GET',
      params: { pageStart, pageSize },
    })

  export const answerQuestion = (questionId: number, content: string) =>
    axiosRequest<AnswerQuestionResponse>({
      url: `/questions/${questionId}/answers`,
      method: 'POST',
      data: { content },
    })

  export const getAnswerDetail = (questionId: number, answerId: number) =>
    axiosRequest<GetAnswerDetailResponse>({
      url: `/questions/${questionId}/answers/${answerId}`,
      method: 'GET',
    })

  export const postAttitude = (questionId: number, answerId: number, attitude: NewAttitudeType) =>
    axiosRequest<AttitudeAnswerResponse>({
      url: `/questions/${questionId}/answers/${answerId}/attitudes`,
      method: 'POST',
      data: { attitude_type: attitude },
    })

  export const favorite = (questionId: number, answerId: number) =>
    axiosMutation({ url: `/questions/${questionId}/answers/${answerId}/favorite`, method: 'PUT' })

  export const unfavorite = (questionId: number, answerId: number) =>
    axiosMutation({ url: `/questions/${questionId}/answers/${answerId}/favorite`, method: 'DELETE' })
}
