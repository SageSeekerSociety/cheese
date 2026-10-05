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

export interface LiveTokenResponse {
  token: string
}
