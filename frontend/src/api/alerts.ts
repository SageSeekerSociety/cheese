import type { InboxItem } from '../cx_types'

import { request } from './http'

export function markRead(alertId: number): Promise<InboxItem> {
  return request<InboxItem>(`/alerts/${alertId}/read`, { method: 'POST' })
}

// 把这个项目里写给我、还没读的通知一次标掉。没拍板的决策请求读过也还留在「待办」里。
export function markAllAlertsRead(projectId: string): Promise<{ marked: number }> {
  return request<{ marked: number }>(`/projects/${encodeURIComponent(projectId)}/alerts/read-all`, { method: 'POST' })
}

// 拍板。答复之后这一条不再等人，「待办」里就没有它了。
export function resolveAlert(alertId: number, chosen: string): Promise<InboxItem> {
  return request<InboxItem>(`/alerts/${alertId}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ chosen }),
  })
}
