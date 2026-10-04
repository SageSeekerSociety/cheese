// 通知设置页要的两个接口：读一个人的通知偏好，和整份写回。
//
// 只服务调用者自己 —— 偏好就是「我自己的通知怎么发」，两个接口都不收指明收件人的
// 参数，收件人就是带着令牌的那个人。读回来没保存过时是设计稿的默认，不是 404；
// 写回是整份替换，回的就是存下的那一份。
import type { NotificationPreferences } from '@/lib/notificationPreferences'

import { request } from '../api'

export function getNotificationPreferences(): Promise<NotificationPreferences> {
  return request<NotificationPreferences>('/notifications/preferences')
}

export function saveNotificationPreferences(prefs: NotificationPreferences): Promise<NotificationPreferences> {
  return request<NotificationPreferences>('/notifications/preferences', {
    method: 'PUT',
    body: JSON.stringify(prefs),
  })
}
