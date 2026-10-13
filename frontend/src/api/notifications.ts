import type {
  ListNotificationsParams,
  ListNotificationsResponse,
  LiveTokenResponse,
  MarkAllAsReadResponse,
  Notification,
  NotificationType,
  UnreadCountResponse,
} from '@/types/apiNotifications'

import { axiosMutation, axiosRequest } from './axiosHttp'

/** Notifications ride the new API origin; JSON calls expose only the payload. */
export namespace NotificationsApi {
  // 获取通知列表
  export const list = (params: ListNotificationsParams) =>
    axiosRequest<ListNotificationsResponse>(
      {
        url: '/notifications',
        method: 'GET',
        // 几种类型拼成一个 `type=`（服务端那个参数就收这个样子）。写在展开后面是
        // 覆盖同一个键，键的位置不动，发出去的 URL 和从前一样。
        params: { ...params, type: Array.isArray(params.type) ? params.type.join(',') : params.type },
      },
      true
    )

  // 每一组类型各有多少条（一组里的几种算在一起）。界面上「动态」那排筛选把数字写在
  // 每一档上，而分页和 total 都是服务端算的——客户端数手上已经翻到的那几页会少。
  // 每组只要一条（`pageSize: 1`），拿的就是 `total`。
  export const countByTypes = (groups: NotificationType[][]) =>
    Promise.all(
      groups.map(async (type) => {
        const page = await list({ type, pageSize: 1 })
        return page.page.total ?? 0
      })
    )

  // 删除通知
  export const del = (notificationId: number) =>
    axiosMutation({ url: `/notifications/${notificationId}`, method: 'DELETE' }, true)

  // 标记单个通知为已读/未读
  export const updateStatus = (notificationId: number, read: boolean) =>
    axiosRequest<{ notification: Notification }>(
      { url: `/notifications/${notificationId}`, method: 'PATCH', data: { read } },
      true
    )

  // 全部标为已读
  export const markAllAsRead = () =>
    axiosRequest<MarkAllAsReadResponse>({ url: '/notifications/status', method: 'PUT', data: { read: true } }, true)

  // 获取未读通知数量
  export const getUnreadNotificationCount = () =>
    axiosRequest<UnreadCountResponse>({ url: '/notifications/unread-count', method: 'GET' }, true)

  // 桌面 app 自己连服务器收通知用的凭证，只能用来开那条连接（lib/desktopApp.ts）
  export const liveToken = () =>
    axiosRequest<LiveTokenResponse>({ url: '/notifications/live-token', method: 'POST' }, true)
}

// 试点期落地的名字（useUnreadNotifications 与既有对拍用例按它取用）。
export const getUnreadNotificationCount = NotificationsApi.getUnreadNotificationCount
