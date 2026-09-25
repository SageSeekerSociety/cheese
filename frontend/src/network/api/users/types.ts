import type { Answer, Page, Question, User } from '@/types'

export type { Page }

export type GetAnswerListResponse = {
  answers: Answer[]
  page: Page
}

export type GetQuestionListResponse = {
  questions: Question[]
  page: Page
}

export type GetUserInfoResponse = {
  user: User
}

export type PasskeyRegistrationOptionsResponse = {
  options: any // 根据实际返回类型调整
}

export type PasskeyAuthenticationOptionsResponse = {
  options: any // 根据实际返回类型调整
}

export type PasskeyInfo = {
  id: string
  createdAt: Date
  deviceType: string
  backedUp: boolean
}

export type GetPasskeysResponse = {
  passkeys: PasskeyInfo[]
}

/** One sign-in: a device that holds a refresh token for the account. */
export type SessionInfo = {
  id: string
  loginMethod: string
  ipAddress: string
  userAgent: string
  createdAt: string
  lastActiveAt: string
  /** The sign-in this request was made from. */
  current: boolean
  /** Its browser is trusted to skip two-step verification. */
  trusted: boolean
}

export type GetSessionsResponse = {
  sessions: SessionInfo[]
}

/** The ways the signed-in account can confirm its identity. */
export type MyAuthMethods = {
  password: boolean
  passkey: boolean
  twoFactor: boolean
  /** A code mailed to the account; never alongside two-step verification. */
  emailCode: boolean
}

export interface TokenPayload {
  payload: {
    authorization: {
      userId: number
      permissions: string[]
      sudoUntil?: number
      username?: string
    }
    signedAt: number
    validUntil: number
  }
}

// 实名信息类型
export interface RealNameInfo {
  realName: string
  studentId: string
  grade: string
  major: string
  className: string
  phone?: string
  email?: string
  isEncrypted?: boolean
}

// 实名信息访问模块类型
export enum UserIdentityAccessModuleType {
  TASK = 'TASK',
}

// 实名信息访问类型
export enum UserIdentityAccessType {
  VIEW = 'VIEW',
  EXPORT = 'EXPORT',
}

// 实名信息访问日志
export interface UserIdentityAccessLog {
  accessor: User
  accessModuleType?: UserIdentityAccessModuleType
  accessEntityId?: number
  accessEntityName?: string
  accessTime: number
  accessType: UserIdentityAccessType
  ipAddress: string
}

export type GetRealNameInfoResponse = {
  hasIdentity: boolean
  identity?: RealNameInfo
}

export type UpdateRealNameInfoResponse = {
  success: boolean
  realNameInfo: RealNameInfo
}

// OAuth 相关类型定义
export interface OAuthProvider {
  id: string
  name: string
  scope: string[]
}

export type GetOAuthProvidersResponse = {
  providers: OAuthProvider[]
}

// OAuth 验证请求类型
export interface OAuthVerifyRequest {
  sessionId: string
  password?: string
}

// OAuth 状态信息类型
export interface OAuthState {
  providerId: string
  userInfo: {
    id: string
    email: string | null
    name: string
    preferredUsername: string
    // Set once the person has proven an address with a code; the account is
    // created with it.
    verifiedEmail?: string
  }
  suggestedUsername: string
  suggestedNickname: string
}

export type GetOAuthStateResponse = OAuthState

// The verify page's query, when the address belongs to an existing account.
export interface OAuthOwnership {
  type: string
  email: string
  sessionId: string
}

export type VerifyOAuthEmailResponse = { stateToken: string; ownership?: undefined } | { ownership: OAuthOwnership }

// OAuth 创建用户请求类型
export interface OAuthCreateUserRequest {
  stateToken: string
  username: string
  nickname: string
  passwordMode: 'none' | 'password'
  password?: string
  inviteCode?: string
  // 建号时的同意（#1486）：两份文档各自的版本，和同意的方式
  consentTerms?: string
  consentPrivacy?: string
  consentMethod?: 'checkbox' | 'dialog'
}

export type OAuthCreateUserResponse = {
  user: {
    id: number
    username: string
    email: string
    nickname: string
  }
  token: string
}

// OAuth 绑定用户请求类型 (传统方式)
export interface OAuthBindUserRequest {
  stateToken: string
  username: string
  password: string
}

export type OAuthBindUserResponse = {
  user: {
    id: number
    username: string
    email: string
  }
  token: string
}

// OAuth 绑定连接响应类型
