import type { User } from '@/types'
import type { AcceptedDocuments, ConsentMethod } from '@/types/apiLegal'
import type {
  GetOAuthProvidersResponse,
  GetOAuthStateResponse,
  MyAuthMethods,
  OAuthBindUserRequest,
  OAuthCreateUserRequest,
  VerifyOAuthEmailResponse,
} from '@/types/apiUsers'

import { axiosMutation, axiosRequest } from './axiosHttp'

// 这个值取自构建时的 VITE_API_BASE_URL，不带兜底。
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

/** 证明「你是谁」：登录、注册、找回、两步验证、sudo 与 OAuth。
 *
 *  登录之后改自己的资料、会话与安全设置的那些接口在 `./users`（`UserApi`）。这里是
 *  唯一会碰到 `/users/auth` 前缀的地方 —— 新栈的 401 刷新拦截器对这条前缀跳过重试
 *  （`axiosHttp.ts`），所以这些方法拿到的 401 原样抛出，与旧栈一致。 */
export namespace UserAuthApi {
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
    | 'passkey:add'
    | 'passkey:delete'
    | 'password:change'
    | 'oauth:unbind'
    | 'realname:view'
    | 'realname:update'
    | 'realname:delete'
    | 'account:delete'

  export interface TOTPAuthResponseDataType extends AuthResponseDataType {
    requires2FA: boolean
    usedBackupCode: boolean
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
    axiosRequest<RegisterResponseDataType>({
      url: '/users',
      method: 'POST',
      data,
      withCredentials: true,
    })

  export const getRegistrationConfig = () =>
    axiosRequest<RegistrationConfigDataType>({ url: '/users/registration-config', method: 'GET' })

  export const login = (data: { username: string; password: string }) =>
    axiosRequest<AuthResponseDataType>({
      url: '/users/auth/login',
      method: 'POST',
      data,
      withCredentials: true,
    })

  export const requestSignInCode = (email: string) =>
    axiosMutation({ url: '/users/auth/email-code', method: 'POST', data: { email } })

  export const signInWithEmailCode = (data: { email: string; code: string }) =>
    axiosRequest<AuthResponseDataType>({
      url: '/users/auth/email-code/verify',
      method: 'POST',
      data,
      withCredentials: true,
    })

  export const sendEmailCode = (email: string, inviteCode?: string) =>
    axiosMutation({
      url: '/users/verify/email',
      method: 'POST',
      data: { email, ...(inviteCode ? { inviteCode } : {}) },
    })

  export const recoverPasswordRequest = (email: string) =>
    axiosMutation({ url: '/users/recover/password/request', method: 'POST', data: { email } })

  export const recoverPasswordVerify = (data: { token: string; password: string }) =>
    axiosMutation({ url: '/users/recover/password/verify', method: 'POST', data })

  // Passkey 认证相关
  export const getPasskeyAuthenticationOptions = (userId?: number) =>
    axiosRequest<{ options: any }>({
      url: '/users/auth/passkey/options',
      method: 'POST',
      data: { userId },
      withCredentials: true,
    })

  export const verifyPasskeyAuthentication = (response: any) =>
    axiosRequest<AuthResponseDataType>({
      url: '/users/auth/passkey/verify',
      method: 'POST',
      data: { response },
      withCredentials: true,
    })

  /**
   * A ticket for `purpose` without proving anything again, granted only while
   * this sign-in is within its sudo window; refused with SudoRequiredError
   * otherwise.
   */
  export const requestSudoTicket = (purpose: SudoPurpose) =>
    axiosRequest<VerifySudoResponse>({ url: '/users/auth/sudo', method: 'POST', data: { purpose } })

  export const verifySudoPassword = (password: string, purpose?: SudoPurpose) =>
    axiosRequest<VerifySudoResponse>({
      url: '/users/auth/sudo',
      method: 'POST',
      data: { method: 'password', credentials: { password }, purpose },
    })

  export const verifySudoPasskey = (response: any, purpose?: SudoPurpose) =>
    axiosRequest<VerifySudoResponse>({
      url: '/users/auth/sudo',
      method: 'POST',
      data: { method: 'passkey', credentials: { passkeyResponse: response }, purpose },
      withCredentials: true,
    })

  export const verifySudoTOTP = (code: string, purpose?: SudoPurpose) =>
    axiosRequest<VerifySudoResponse>({
      url: '/users/auth/sudo',
      method: 'POST',
      data: { method: 'totp', credentials: { code }, purpose },
    })

  /** Mail a code that confirms the signed-in user's identity to their own address. */
  export const requestSudoEmailCode = () =>
    axiosRequest<{ email: string }>({ url: '/users/me/sudo/email-code', method: 'POST' })

  export const verifySudoEmailCode = (code: string, purpose?: SudoPurpose) =>
    axiosRequest<VerifySudoResponse>({
      url: '/users/auth/sudo',
      method: 'POST',
      data: { method: 'email_code', credentials: { code }, purpose },
    })

  export const getMyAuthMethods = () => axiosRequest<MyAuthMethods>({ url: '/users/me/auth-methods', method: 'GET' })

  // TOTP 验证相关
  /** `trust_device` trusts this browser to skip the step for 30 days. */
  export const verify2FA = (data: { temp_token: string; code: string; trust_device: boolean }) =>
    axiosRequest<TOTPAuthResponseDataType>({
      url: '/users/auth/verify-2fa',
      method: 'POST',
      data,
      withCredentials: true,
    })

  export const logout = () => axiosMutation({ url: '/users/auth/logout', method: 'POST', withCredentials: true })

  // 在浏览器里登录后交给桌面 app（views/account/BackToApp.vue、AppSignInFinish.vue）
  // `returnTo`: another origin of this deployment the code is carried back to (lib/desktopApp.ts signInAtOrigin).
  export const startAppSignIn = (challenge: string, returnTo?: string) =>
    axiosRequest<{ code: string }>({
      url: '/users/auth/app-sign-in',
      method: 'POST',
      data: { challenge, ...(returnTo ? { returnTo } : {}) },
    })

  export const finishAppSignIn = (code: string, verifier: string) =>
    axiosRequest<null>({ url: '/users/auth/app-sign-in/finish', method: 'POST', data: { code, verifier } })

  // OAuth 相关 API
  export const getOAuthProviders = () =>
    axiosRequest<GetOAuthProvidersResponse>({ url: '/users/auth/oauth/providers', method: 'GET' })

  export const getGithubAppIntent = (returnPath: string) =>
    axiosRequest<{ intent: string }>({
      url: '/users/auth/oauth/github-app/intent',
      method: 'POST',
      data: { returnPath },
    })

  export const oauthLoginUrl = (providerId: string, state?: string, accessType?: string) => {
    const params = new URLSearchParams()
    if (state) params.append('state', state)
    if (accessType) params.append('access_type', accessType)

    const queryString = params.toString()
    const url = `/users/auth/oauth/login/${providerId}${queryString ? `?${queryString}` : ''}`

    return `${API_BASE_URL}${url}`
  }

  export const redirectToOAuthLogin = (providerId: string, state?: string, accessType?: string) => {
    // 直接跳转到后端 OAuth 登录 URL
    window.location.href = oauthLoginUrl(providerId, state, accessType)
  }

  // OAuth 验证 API
  export const verifyOAuth = (data: { sessionId: string; password?: string }) =>
    axiosMutation({ url: '/users/auth/oauth/verify', method: 'POST', data })

  // 获取 OAuth 状态信息 (决策页面使用)
  export const getOAuthState = (stateToken: string) =>
    axiosRequest<GetOAuthStateResponse>({
      url: `/users/auth/oauth/state?token=${encodeURIComponent(stateToken)}`,
      method: 'GET',
    })

  // The address a new third-party account will hold is proven with a code.
  export const sendOAuthEmailCode = (data: { stateToken: string; email: string }) =>
    axiosMutation({ url: '/users/auth/oauth/email/code', method: 'POST', data })

  export const verifyOAuthEmail = (data: { stateToken: string; email: string; code: string }) =>
    axiosRequest<VerifyOAuthEmailResponse>({ url: '/users/auth/oauth/email/verify', method: 'POST', data })

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
