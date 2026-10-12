import { axiosRequest } from './axiosHttp'

export function getUnreadNotificationCount(): Promise<{ count: number }> {
  return axiosRequest({ url: '/notifications/unread-count', method: 'GET' }, true)
}
