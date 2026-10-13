import type {
  ListNotificationsParams,
  ListNotificationsResponse,
  LiveTokenResponse,
  MarkAllAsReadResponse,
  Notification,
  UnreadCountResponse,
} from '@/types/apiNotifications'

import { changesAwaiting } from './awaitingChanged'
import { axiosMutation, axiosRequest } from './axiosHttp'

/** Notifications ride the new API origin; JSON calls expose only the payload. */
export namespace NotificationsApi {
  // 获取通知列表
  export const list = (params: ListNotificationsParams) =>
    axiosRequest<ListNotificationsResponse>({ url: '/notifications', method: 'GET', params }, true)

  // 删除通知。删掉、读过的变更提醒都不再在「待办」里，所以这三个写都告诉角标重读。
  export const del = (notificationId: number) =>
    changesAwaiting(axiosMutation({ url: `/notifications/${notificationId}`, method: 'DELETE' }, true))

  // 标记单个通知为已读/未读
  export const updateStatus = (notificationId: number, read: boolean) =>
    changesAwaiting(
      axiosRequest<{ notification: Notification }>(
        { url: `/notifications/${notificationId}`, method: 'PATCH', data: { read } },
        true
      )
    )

  // 全部标为已读
  export const markAllAsRead = () =>
    changesAwaiting(
      axiosRequest<MarkAllAsReadResponse>({ url: '/notifications/status', method: 'PUT', data: { read: true } }, true)
    )

  // 获取未读通知数量
  export const getUnreadNotificationCount = () =>
    axiosRequest<UnreadCountResponse>({ url: '/notifications/unread-count', method: 'GET' }, true)

  // 桌面 app 自己连服务器收通知用的凭证，只能用来开那条连接（lib/desktopApp.ts）
  export const liveToken = () =>
    axiosRequest<LiveTokenResponse>({ url: '/notifications/live-token', method: 'POST' }, true)
}

// 试点期落地的名字（useUnreadNotifications 与既有对拍用例按它取用）。
export const getUnreadNotificationCount = NotificationsApi.getUnreadNotificationCount
