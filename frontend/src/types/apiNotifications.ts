// 通知的形状，集中在这里，
// 组件（不碰 API 层）也能按名取类型。
import type { EncodedCursorPage } from '@/types'

export type NotificationType =
  | 'MENTION'
  | 'REPLY'
  // 我参与过的支线里有人回复了。payload 带 thread_id，topic_id 是支线所在的频道。
  | 'THREAD_REPLY'
  | 'REACTION'
  | 'PROJECT_INVITE'
  | 'DEADLINE_REMIND'
  | 'TEAM_JOIN_REQUEST'
  | 'TEAM_INVITATION'
  | 'TEAM_REQUEST_APPROVED'
  | 'TEAM_REQUEST_REJECTED'
  | 'TEAM_INVITATION_ACCEPTED'
  | 'TEAM_INVITATION_DECLINED'
  | 'TEAM_INVITATION_CANCELED'
  | 'TEAM_REQUEST_CANCELED'
  | 'ROOM_NOTICE'
  | 'CHEESE_QUESTION'
  | 'DEVICE_IN_USE'
  | 'SPACE_ANNOUNCEMENT'

export interface EntityInfo {
  id: string
  type: string
  name: string
  // The page this entity opens, as the backend resolved it (a team: `/teams/<handle>`).
  url?: string
  avatarUrl?: string
  // A user entity's handle, so the name can link to that person.
  handle?: string | null
  // Where the entity stands now, for the kinds that have a state (a team
  // invitation or join request: PENDING, ACCEPTED, DECLINED, ...).
  status?: string | null
}

export interface Notification {
  id: number
  type: NotificationType
  read: boolean
  createdAt: number
  updatedAt?: number
  entities: Record<string, EntityInfo | null>
  contextMetadata: Record<string, any>
}

export interface ListNotificationsParams {
  // 要哪一种，或者要哪几种里的任意一种。线上的参数只有一个、是字符串，几种就逗号
  // 分开（`?type=MENTION,REPLY`，服务端那个 `type` 就收这个样子），所以两种写法到
  // 服务端是同一个请求，API 层负责拼。
  type?: NotificationType | NotificationType[]
  read?: boolean
  pageStart?: string
  pageSize: number
}

export interface ListNotificationsResponse {
  notifications: Notification[]
  page: EncodedCursorPage
}

export interface NotificationUpdate {
  id: number
  read: boolean
}

export interface BulkUpdateRequest {
  updates: NotificationUpdate[]
}

export interface BulkUpdateResponse {
  updatedIds: number[]
}

export interface MarkAllAsReadResponse {
  count: number
}

export interface UnreadCountResponse {
  count: number
}

export interface LiveTokenResponse {
  token: string
}
