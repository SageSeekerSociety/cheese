// 用户接口的形状，集中在这里，
// 供 `@/api/users`、`@/api/userAuth` 与它们的消费者共用。

import type { Answer, Page, Question, User } from '@/types'

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

/** A real-name record. Grade, major and class may be empty. */
export interface RealNameInfo {
  realName: string
  studentId: string
  grade: string
  major: string
  className: string
}

export enum UserIdentityAccessType {
  VIEW = 'VIEW',
  EXPORT = 'EXPORT',
}

/** One read of a person's real-name record: who, how, where and when. */
export interface UserIdentityAccessLog {
  accessor: User
  /** `SPACE` for a read on a board; older entries may carry something else, or nothing. */
  accessModuleType?: string | null
  accessEntityId?: number | null
  /** The board's name, for a read on a board. */
  accessEntityName?: string | null
  accessTime: number
  accessType: UserIdentityAccessType
}

export type GetRealNameInfoResponse = {
  hasIdentity: boolean
  identity?: RealNameInfo | null
}

export type UpdateRealNameInfoResponse = {
  identity: RealNameInfo
}

// OAuth 相关类型定义
export interface OAuthProvider {
  id: string
  name: string
  scope: string[]
  // The origin this provider's sign-in runs on, when the deployment is served
  // under several and the provider's callback is registered on one of them.
  origin?: string | null
}

export type GetOAuthProvidersResponse = {
  providers: OAuthProvider[]
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

// OAuth 绑定用户请求类型 (传统方式)
export interface OAuthBindUserRequest {
  stateToken: string
  username: string
  password: string
}
