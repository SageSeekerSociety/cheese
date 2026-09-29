import type { EncodedCursorPage } from '@/types'

export type NotificationType =
  | 'MENTION'
  | 'REPLY'
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

export interface EntityInfo {
  id: string
  type: string
  name: string
  // The page this entity opens, as the backend resolved it (a team: `/teams/<handle>`).
  url?: string
  avatarUrl?: string
  // A user entity's handle, so the name can link to that person.
  handle?: string | null
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
  type?: NotificationType
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

export interface MarkAllAsReadRequest {
  read: boolean
}

export interface MarkAllAsReadResponse {
  count: number
}

export interface UnreadCountResponse {
  count: number
}

/** 一条本该推送的通知，标题和正文与推送上的一样；`url` 是点开后去的站内路径。 */
export interface PushFeedItem {
  id: number
  title: string
  body: string
  url: string
}

export interface PushFeedResponse {
  /** 下一次从这里接着取；还没有任何一条时为 null。 */
  latest: number | null
  items: PushFeedItem[]
}
