import type { User } from '@/types'
import type { AcceptedDocuments, ConsentMethod } from '../legal/types'
import type {
  AuthMethodsResponse,
  FollowUserResponse,
  GetAnswerListResponse,
  GetOAuthProvidersResponse,
  GetOAuthStateResponse,
  GetPasskeysResponse,
  GetQuestionListResponse,
  GetRealNameInfoResponse,
  GetSessionsResponse,
  GetUserInfoResponse,
  OAuthBindUserRequest,
  OAuthBindUserResponse,
  OAuthCreateUserRequest,
  OAuthCreateUserResponse,
  Page,
  RealNameInfo,
  UpdateRealNameInfoResponse,
  UserIdentityAccessLog,
  UserList,
  VerifyOAuthEmailResponse,
} from './types'

import { API_BASE_URL } from '../../utils'
import ApiInstance, { NewApiInstance } from '../index'

export namespace UserApi {
  export interface AuthResponseDataType {
    user?: User
    accessToken?: string
    requires2FA?: boolean
    tempToken?: string
    passkeyEnrollment?: PasskeyEnrollment
  }

  /**
   * What a finished sign-in hands back for adding a passkey: a ticket that
   * opens one registration within a few minutes, and whether this account is
   * due the screen offering one.
   */
  export interface PasskeyEnrollment {
    ticket: string
    offer: boolean
    canStopAsking: boolean
  }

  export type RegisterResponseDataType = {
    user: User
    accessToken: string
  }

  export type RegistrationConfigDataType = {
    requireInviteCode: boolean
  }

  export const register = (data: {
    username: string
    nickname: string
    password: string
    email: string
    emailCode: string
    inviteCode?: string
    consent?: { documents: AcceptedDocuments; method: ConsentMethod }
  }) =>
    ApiInstance.request<RegisterResponseDataType>({
      url: '/users',
      method: 'POST',
      data,
      withCredentials: true,
    })

  export const getRegistrationConfig = () =>
    ApiInstance.request<RegistrationConfigDataType>({
      url: '/users/registration-config',
      method: 'GET',
    })

  export const login = (data: { username: string; password: string }) =>
    ApiInstance.request<AuthResponseDataType>({
      url: '/users/auth/login',
      method: 'POST',
      data,
      withCredentials: true,
    })

  export const sendEmailCode = (email: string, inviteCode?: string) =>
    ApiInstance.request({
      url: '/users/verify/email',
      method: 'POST',
      data: { email, ...(inviteCode ? { inviteCode } : {}) },
    })

  export const recoverPasswordRequest = (email: string) =>
    ApiInstance.request({
      url: '/users/recover/password/request',
      method: 'POST',
      data: { email },
    })

  export const recoverPasswordVerify = (data: { token: string; password: string }) =>
    ApiInstance.request({
      url: '/users/recover/password/verify',
      method: 'POST',
      data,
    })

  export const getCurrentUser = () =>
    ApiInstance.request<GetUserInfoResponse>({
      url: '/users/me',
      method: 'GET',
    })

  export const getUserInfo = (userid: number) =>
    ApiInstance.request<GetUserInfoResponse>({
      // url: `https://stoplight.io/mocks/huanchengstudio/cheese/2398548/users/${userid}`,
      url: `/users/${userid}`,
      method: 'GET',
    })

  export const updateUserInfo = (userid: number, data: { nickname: string; intro: string; avatarId: number }) =>
    ApiInstance.request({
      url: `/users/${userid}`,
      method: 'PUT',
      data,
    })

  export const getUserFollower = (userid: number, data: { pageStart: number; pageSize: number }) =>
    ApiInstance.request<UserList>({
      // url: `https://stoplight.io/mocks/huanchengstudio/cheese/2398548/users/${userid}/followers`,
      url: `/users/${userid}/followers`,
      method: 'GET',
      data: {
        pageStart: data.pageStart,
        pageSize: data.pageSize,
      },
    })

  export const getUserFollowing = (userid: number, data: { pageStart: number; pageSize: number }) =>
    ApiInstance.request<UserList>({
      // url: `https://stoplight.io/mocks/huanchengstudio/cheese/2398548/users/${userid}/follow/users`,
      url: `/users/${userid}/follow/users`,
      method: 'GET',
      data: {
        pageStart: data.pageStart,
        pageSize: data.pageSize,
      },
    })

  export const getQuestionList = (userId: number, pageStart?: number, pageSize: number = 20) =>
    ApiInstance.request<GetQuestionListResponse>({
      // url: `https://stoplight.io/mocks/huanchengstudio/cheese/2398548/users/${userid}/questions`,
      url: `/users/${userId}/questions`,
      method: 'GET',
      data: {
        pageStart: pageStart,
        pageSize: pageSize,
      },
    })

  export const getAnswerList = (userid: number, data: { pageStart: number; pageSize: number }) =>
    ApiInstance.request<GetAnswerListResponse>({
      // url: `https://stoplight.io/mocks/huanchengstudio/cheese/2398548/users/${userid}/answers`,
      url: `/users/${userid}/answers`,
      method: 'GET',
      data: {
        pageStart: data.pageStart,
        pageSize: data.pageSize,
      },
    })

  export const followUser = (userId: number) =>
    ApiInstance.request<FollowUserResponse>({
      url: `/users/${userId}/followers`,
      method: 'POST',
    })

  export const unfollowUser = (userId: number) =>
    ApiInstance.request<FollowUserResponse>({
      url: `/users/${userId}/followers`,
      method: 'DELETE',
    })

  // Passkey 注册相关
  export const getPasskeyRegistrationOptions = (userId: number, sudoTicket: string) =>
    ApiInstance.request<{ options: any }>({
      url: `/users/${userId}/passkeys/options`,
      method: 'POST',
      data: { sudoTicket },
      withCredentials: true,
    })

  export const verifyPasskeyRegistration = (userId: number, response: any) =>
    ApiInstance.request({
      url: `/users/${userId}/passkeys`,
      method: 'POST',
      data: { response },
      withCredentials: true,
    })

  /** Decline the offer to add a passkey shown after signing in; `forever`
   *  stops it for good instead of holding it back for a while. */
  export const dismissPasskeyPrompt = (userId: number, forever: boolean) =>
    ApiInstance.request({
      url: `/users/${userId}/passkeys/prompt/dismiss`,
      method: 'POST',
      data: { forever },
    })

  // Passkey 认证相关
  export const getPasskeyAuthenticationOptions = (userId?: number) =>
    ApiInstance.request<{ options: any }>({
      url: '/users/auth/passkey/options',
      method: 'POST',
      data: { userId },
      withCredentials: true,
    })

  export const verifyPasskeyAuthentication = (response: any) =>
    ApiInstance.request<AuthResponseDataType>({
      url: '/users/auth/passkey/verify',
      method: 'POST',
      data: { response },
      withCredentials: true,
    })

  // Passkey 管理相关
  export const listSessions = () =>
    ApiInstance.request<GetSessionsResponse>({
      url: '/users/me/sessions',
      method: 'GET',
    })

  export const revokeSession = (sessionId: string) =>
    ApiInstance.request({
      url: `/users/me/sessions/${encodeURIComponent(sessionId)}`,
      method: 'DELETE',
    })

  export const revokeOtherSessions = () =>
    ApiInstance.request<{ revokedCount: number }>({
      url: '/users/me/sessions',
      method: 'DELETE',
    })

  export const getUserPasskeys = (userId: number) =>
    ApiInstance.request<GetPasskeysResponse>({
      url: `/users/${userId}/passkeys`,
      method: 'GET',
    })

  export const deletePasskey = (userId: number, credentialId: string, sudoTicket: string) =>
    ApiInstance.request({
      url: `/users/${userId}/passkeys/${credentialId}`,
      method: 'DELETE',
      data: { sudoTicket },
    })

  /**
   * What re-authenticating buys. `sudoTicket` is the server's own proof that
   * it happened, redeemable once; it comes back only when a purpose was asked
   * for, since operations the server does not gate have nothing to redeem it.
   */
  export type VerifySudoResponse = {
    verified: boolean
    sudoTicket?: string
  }

  /** The privileged operations the server gates on a sudo ticket. */
  export type SudoPurpose =
    | '2fa:enable'
    | '2fa:disable'
    | '2fa:backup-codes'
    | '2fa:settings'
    | 'passkey:add'
    | 'passkey:delete'
    | 'password:change'
    | 'oauth:unbind'
    | 'realname:view'
    | 'realname:update'

  export const verifySudoPassword = (password: string, purpose?: SudoPurpose) =>
    ApiInstance.request<VerifySudoResponse>({
      url: '/users/auth/sudo',
      method: 'POST',
      data: {
        method: 'password',
        credentials: { password },
        purpose,
      },
    })

  export const verifySudoPasskey = (response: any, purpose?: SudoPurpose) =>
    ApiInstance.request<VerifySudoResponse>({
      url: '/users/auth/sudo',
      method: 'POST',
      data: {
        method: 'passkey',
        credentials: { passkeyResponse: response },
        purpose,
      },
      withCredentials: true,
    })

  export const logout = () =>
    ApiInstance.request({
      url: '/users/auth/logout',
      method: 'POST',
      withCredentials: true,
    })

  export interface TOTPAuthResponseDataType extends AuthResponseDataType {
    requires2FA: boolean
    usedBackupCode: boolean
  }

  export interface Enable2FAResponseDataType {
    secret: string
    otpauth_url: string
    qrcode: string
    backup_codes: string[]
  }

  export interface GenerateBackupCodesResponseDataType {
    backup_codes: string[]
  }

  // TOTP 验证相关
  export const verify2FA = (data: { temp_token: string; code: string }) =>
    ApiInstance.request<TOTPAuthResponseDataType>({
      url: '/users/auth/verify-2fa',
      method: 'POST',
      data,
      withCredentials: true,
    })

  // TOTP 管理相关
  export const initializeTOTP = (userId: number, sudoTicket: string) =>
    ApiInstance.request<Enable2FAResponseDataType>({
      url: `/users/${userId}/2fa/enable`,
      method: 'POST',
      data: { sudoTicket },
    })

  export const enableTOTP = (userId: number, data: { code: string; secret: string }) =>
    ApiInstance.request<Enable2FAResponseDataType>({
      url: `/users/${userId}/2fa/enable`,
      method: 'POST',
      data,
    })

  export const disableTOTP = (userId: number, sudoTicket: string) =>
    ApiInstance.request({
      url: `/users/${userId}/2fa/disable`,
      method: 'POST',
      data: { sudoTicket },
    })

  export const generateBackupCodes = (userId: number, sudoTicket: string) =>
    ApiInstance.request<GenerateBackupCodesResponseDataType>({
      url: `/users/${userId}/2fa/backup-codes`,
      method: 'POST',
      data: { sudoTicket },
    })

  export interface Get2FAStatusResponseDataType {
    enabled: boolean
    has_passkey: boolean
    always_required: boolean
  }

  export interface Update2FASettingsResponseDataType {
    success: boolean
    always_required: boolean
  }

  // 获取 2FA 状态
  export const get2FAStatus = (userId: number) =>
    ApiInstance.request<Get2FAStatusResponseDataType>({
      url: `/users/${userId}/2fa/status`,
      method: 'GET',
    })

  // 添加更新 2FA 设置的方法
  export const update2FASettings = (userId: number, alwaysRequired: boolean, sudoTicket: string) =>
    ApiInstance.request<Update2FASettingsResponseDataType>({
      url: `/users/${userId}/2fa/settings`,
      method: 'PUT',
      data: { always_required: alwaysRequired, sudoTicket },
    })

  export const verifySudoTOTP = (code: string, purpose?: SudoPurpose) =>
    ApiInstance.request<VerifySudoResponse>({
      url: '/users/auth/sudo',
      method: 'POST',
      data: {
        method: 'totp',
        credentials: { code },
        purpose,
      },
    })

  // 获取认证方法
  export const getAuthMethods = (username: string) =>
    ApiInstance.request<AuthMethodsResponse>({
      url: `/users/auth/methods/${username}`,
      method: 'GET',
    })

  export const changePassword = (
    userId: number,
    data: {
      password: string
      sudoTicket: string
    }
  ) =>
    ApiInstance.request({
      url: `/users/${userId}/password`,
      method: 'PATCH',
      data,
    })

  // 实名信息 API。看完整信息和修改都要一张 sudo 票：看要 'realname:view'，改要 'realname:update'
  export const getRealNameInfo = (userId: number) =>
    NewApiInstance.request<GetRealNameInfoResponse>({
      url: `/users/${userId}/identity`,
      method: 'GET',
      params: { precise: false },
    })

  export const getPreciseRealNameInfo = (userId: number, sudoTicket: string) =>
    NewApiInstance.request<GetRealNameInfoResponse>({
      url: `/users/${userId}/identity`,
      method: 'GET',
      params: { precise: true, sudoTicket },
    })

  export const updateRealNameInfo = (userId: number, data: RealNameInfo, sudoTicket: string) =>
    NewApiInstance.request<UpdateRealNameInfoResponse>({
      url: `/users/${userId}/identity`,
      method: 'PUT',
      data: { ...data, sudoTicket },
    })

  export const patchRealNameInfo = (userId: number, data: Partial<RealNameInfo>, sudoTicket: string) =>
    NewApiInstance.request<UpdateRealNameInfoResponse>({
      url: `/users/${userId}/identity`,
      method: 'PATCH',
      data: { ...data, sudoTicket },
    })

  // 获取实名信息访问日志
  export const getRealNameAccessLogs = (userId: number, pageStart?: number, pageSize: number = 20) =>
    NewApiInstance.request<{
      logs: UserIdentityAccessLog[]
      page: Page
    }>({
      url: `/users/${userId}/identity/access-logs`,
      method: 'GET',
      params: {
        pageStart: pageStart,
        pageSize: pageSize,
      },
    })

  // OAuth 相关 API
  export const getOAuthProviders = () =>
    ApiInstance.request<GetOAuthProvidersResponse>({
      url: '/users/auth/oauth/providers',
      method: 'GET',
    })

  export const redirectToOAuthLogin = (providerId: string, state?: string, accessType?: string) => {
    const params = new URLSearchParams()
    if (state) params.append('state', state)
    if (accessType) params.append('access_type', accessType)

    const queryString = params.toString()
    const url = `/users/auth/oauth/login/${providerId}${queryString ? `?${queryString}` : ''}`

    // 直接跳转到后端 OAuth 登录 URL
    window.location.href = `${API_BASE_URL}${url}`
  }

  // OAuth 验证 API
  export const verifyOAuth = (data: { sessionId: string; password?: string }) =>
    ApiInstance.request({
      url: '/users/auth/oauth/verify',
      method: 'POST',
      data,
    })

  // 获取 OAuth 状态信息 (决策页面使用)
  export const getOAuthState = (stateToken: string) =>
    ApiInstance.request<GetOAuthStateResponse>({
      url: `/users/auth/oauth/state?token=${encodeURIComponent(stateToken)}`,
      method: 'GET',
    })

  // The address a new third-party account will hold is proven with a code.
  export const sendOAuthEmailCode = (data: { stateToken: string; email: string }) =>
    ApiInstance.request({
      url: '/users/auth/oauth/email/code',
      method: 'POST',
      data,
    })

  export const verifyOAuthEmail = (data: { stateToken: string; email: string; code: string }) =>
    ApiInstance.request<VerifyOAuthEmailResponse>({
      url: '/users/auth/oauth/email/verify',
      method: 'POST',
      data,
    })

  // 从 OAuth 创建新用户 (通过表单提交，会重定向)
  export const createUserFromOAuth = (data: OAuthCreateUserRequest) => {
    const form = document.createElement('form')
    form.method = 'POST'
    form.action = `${API_BASE_URL}/users/oauth/create`
    form.style.display = 'none'

    Object.entries(data).forEach(([key, value]) => {
      const input = document.createElement('input')
      input.type = 'hidden'
      input.name = key
      input.value = value
      form.appendChild(input)
    })

    document.body.appendChild(form)
    form.submit()
  }

  // 绑定 OAuth 到现有用户 (通过表单提交，会重定向)
  export const bindOAuthToUser = (data: OAuthBindUserRequest) => {
    const form = document.createElement('form')
    form.method = 'POST'
    form.action = `${API_BASE_URL}/users/oauth/bind`
    form.style.display = 'none'

    Object.entries(data).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        const input = document.createElement('input')
        input.type = 'hidden'
        input.name = key
        input.value = value
        form.appendChild(input)
      }
    })

    document.body.appendChild(form)
    form.submit()
  }
}
