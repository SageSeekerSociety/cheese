import type { User } from '@/types'
import type {
  GetAnswerListResponse,
  GetPasskeysResponse,
  GetQuestionListResponse,
  GetRealNameInfoResponse,
  GetSessionsResponse,
  GetUserInfoResponse,
  Page,
  RealNameInfo,
  UpdateRealNameInfoResponse,
  UserIdentityAccessLog,
} from '@/types/apiUsers'

import { axiosMutation, axiosRequest } from './axiosHttp'

/** 已登录账号对自己的操作：资料、会话、passkey、两步验证与实名。
 *
 *  证明「你是谁」、把身份换成一个登录态的那些接口在 `./userAuth`（`UserAuthApi`），
 *  这里只管登录之后能改什么、能看什么。两条线都走主 API 源，只有实名那五个方法
 *  走新 API 源（`axiosRequest(config, true)`），与旧栈 `NewApiInstance` 一致。 */
export namespace UserApi {
  export interface Enable2FAResponseDataType {
    secret: string
    otpauth_url: string
    qrcode: string
    backup_codes: string[]
  }

  export interface GenerateBackupCodesResponseDataType {
    backup_codes: string[]
  }

  export interface Get2FAStatusResponseDataType {
    enabled: boolean
    has_passkey: boolean
  }

  export const getCurrentUser = () => axiosRequest<GetUserInfoResponse>({ url: '/users/me', method: 'GET' })

  /** The UI language the signed-in person picked; their notifications follow it. */
  export const setLanguage = (language: string) =>
    axiosRequest<{ language: string }>({ url: '/users/me/language', method: 'PUT', data: { language } })

  /** The time zone of the signed-in person's browser; their quiet hours run on it. */
  export const setTimezone = (timezone: string) =>
    axiosRequest<{ timezone: string }>({ url: '/users/me/timezone', method: 'PUT', data: { timezone } })

  export const getUserInfo = (userid: number) =>
    axiosRequest<GetUserInfoResponse>({ url: `/users/${userid}`, method: 'GET' })

  /** Changes only the fields given; the rest stay as they are. */
  export const updateUserInfo = (userid: number, data: { nickname?: string; intro?: string; avatarId?: number }) =>
    axiosMutation({ url: `/users/${userid}`, method: 'PATCH', data })

  export const getQuestionList = (userId: number, pageStart?: number, pageSize: number = 20) =>
    axiosRequest<GetQuestionListResponse>({
      url: `/users/${userId}/questions`,
      method: 'GET',
      data: { pageStart: pageStart, pageSize: pageSize },
    })

  export const getAnswerList = (userid: number, data: { pageStart: number; pageSize: number }) =>
    axiosRequest<GetAnswerListResponse>({
      url: `/users/${userid}/answers`,
      method: 'GET',
      data: { pageStart: data.pageStart, pageSize: data.pageSize },
    })

  // Passkey 注册相关
  export const getPasskeyRegistrationOptions = (userId: number, sudoTicket: string) =>
    axiosRequest<{ options: any }>({
      url: `/users/${userId}/passkeys/options`,
      method: 'POST',
      data: { sudoTicket },
      withCredentials: true,
    })

  export const verifyPasskeyRegistration = (userId: number, response: any) =>
    axiosMutation({
      url: `/users/${userId}/passkeys`,
      method: 'POST',
      data: { response },
      withCredentials: true,
    })

  /** Decline the offer to add a passkey shown after signing in; `forever`
   *  stops it for good instead of holding it back for a while. */
  export const dismissPasskeyPrompt = (userId: number, forever: boolean) =>
    axiosMutation({ url: `/users/${userId}/passkeys/prompt/dismiss`, method: 'POST', data: { forever } })

  export const listSessions = () => axiosRequest<GetSessionsResponse>({ url: '/users/me/sessions', method: 'GET' })

  export const revokeSession = (sessionId: string) =>
    axiosMutation({ url: `/users/me/sessions/${encodeURIComponent(sessionId)}`, method: 'DELETE' })

  export const revokeOtherSessions = () =>
    axiosRequest<{ revokedCount: number }>({ url: '/users/me/sessions', method: 'DELETE' })

  export const getUserPasskeys = (userId: number) =>
    axiosRequest<GetPasskeysResponse>({ url: `/users/${userId}/passkeys`, method: 'GET' })

  export const deletePasskey = (userId: number, credentialId: string, sudoTicket: string) =>
    axiosMutation({ url: `/users/${userId}/passkeys/${credentialId}`, method: 'DELETE', data: { sudoTicket } })

  // TOTP 管理相关
  export const initializeTOTP = (userId: number, sudoTicket: string) =>
    axiosRequest<Enable2FAResponseDataType>({
      url: `/users/${userId}/2fa/enable`,
      method: 'POST',
      data: { sudoTicket },
    })

  export const enableTOTP = (userId: number, data: { code: string; secret: string }) =>
    axiosRequest<Enable2FAResponseDataType>({ url: `/users/${userId}/2fa/enable`, method: 'POST', data })

  export const disableTOTP = (userId: number, sudoTicket: string) =>
    axiosMutation({ url: `/users/${userId}/2fa/disable`, method: 'POST', data: { sudoTicket } })

  export const generateBackupCodes = (userId: number, sudoTicket: string) =>
    axiosRequest<GenerateBackupCodesResponseDataType>({
      url: `/users/${userId}/2fa/backup-codes`,
      method: 'POST',
      data: { sudoTicket },
    })

  // 获取 2FA 状态
  export const get2FAStatus = (userId: number) =>
    axiosRequest<Get2FAStatusResponseDataType>({ url: `/users/${userId}/2fa/status`, method: 'GET' })

  export const changePassword = (userId: number, data: { password: string; sudoTicket: string }) =>
    axiosMutation({ url: `/users/${userId}/password`, method: 'PATCH', data })

  // 实名信息 API。看完整信息、修改和删除各要一张对应用途的 sudo 票
  export const getRealNameInfo = (userId: number) =>
    axiosRequest<GetRealNameInfoResponse>(
      { url: `/users/${userId}/identity`, method: 'GET', params: { precise: false } },
      true
    )

  export const getPreciseRealNameInfo = (userId: number, sudoTicket: string) =>
    axiosRequest<GetRealNameInfoResponse>(
      { url: `/users/${userId}/identity`, method: 'GET', params: { precise: true, sudoTicket } },
      true
    )

  export const updateRealNameInfo = (userId: number, data: RealNameInfo, sudoTicket: string) =>
    axiosRequest<UpdateRealNameInfoResponse>(
      { url: `/users/${userId}/identity`, method: 'PUT', data: { ...data, sudoTicket } },
      true
    )

  export const deleteRealNameInfo = (userId: number, sudoTicket: string) =>
    axiosMutation({ url: `/users/${userId}/identity`, method: 'DELETE', data: { sudoTicket } }, true)

  export const getRealNameAccessLogs = (userId: number, pageStart?: number, pageSize: number = 20) =>
    axiosRequest<{ logs: UserIdentityAccessLog[]; page: Page }>(
      {
        url: `/users/${userId}/identity/access-logs`,
        method: 'GET',
        params: { pageStart: pageStart, pageSize: pageSize },
      },
      true
    )

  // An account without an address of its own adds one.
  export const sendAddEmailCode = (email: string) =>
    axiosMutation({ url: '/users/me/email/code', method: 'POST', data: { email } })

  export const addEmail = (data: { email: string; code: string }) =>
    axiosRequest<{ user: User }>({ url: '/users/me/email', method: 'POST', data })
}
