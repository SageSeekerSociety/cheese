import type {
  BulkUpdateRequest,
  BulkUpdateResponse,
  ListNotificationsParams,
  ListNotificationsResponse,
  MarkAllAsReadRequest,
  MarkAllAsReadResponse,
  Notification,
  NotificationUpdate,
  PushFeedResponse,
  UnreadCountResponse,
} from './types'

import { NewApiInstance } from '../index'

export namespace NotificationsApi {
  // 获取通知列表
  export const list = (params: ListNotificationsParams) =>
    NewApiInstance.request<ListNotificationsResponse>({
      url: '/notifications',
      method: 'GET',
      params,
    })

  // 获取单个通知
  export const getById = (notificationId: number) =>
    NewApiInstance.request<{ notification: Notification }>({
      url: `/notifications/${notificationId}`,
      method: 'GET',
    })

  // 删除通知
  export const del = (notificationId: number) =>
    NewApiInstance.request({
      url: `/notifications/${notificationId}`,
      method: 'DELETE',
    })

  // 标记单个通知为已读/未读
  export const updateStatus = (notificationId: number, read: boolean) =>
    NewApiInstance.request<{ notification: Notification }>({
      url: `/notifications/${notificationId}`,
      method: 'PATCH',
      data: { read },
    })

  // 批量更新通知状态
  export const bulkUpdate = (data: BulkUpdateRequest) =>
    NewApiInstance.request<BulkUpdateResponse>({
      url: '/notifications',
      method: 'PATCH',
      data,
    })

  // 全部标记为已读
  export const markAllAsRead = () =>
    NewApiInstance.request<MarkAllAsReadResponse>({
      url: '/notifications/status',
      method: 'PUT',
      data: { read: true },
    })

  // 获取未读通知数量
  export const getUnreadCount = () =>
    NewApiInstance.request<UnreadCountResponse>({
      url: '/notifications/unread-count',
      method: 'GET',
    })

  // 浏览器推送会发的那几条，给收不到推送的桌面 app 来取（lib/desktopNotices.ts）
  export const pushFeed = (after: number | null) =>
    NewApiInstance.request<PushFeedResponse>({
      url: '/notifications/push-feed',
      method: 'GET',
      params: after === null ? {} : { after },
    })
}
