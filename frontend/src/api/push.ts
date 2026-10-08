import { request } from './http'

/** 订阅浏览器推送要用的 VAPID 公钥；这个部署没开推送时 `key` 是 null。 */
export function pushPublicKey(): Promise<{ key: string | null; available: boolean }> {
  return request<{ key: string | null; available: boolean }>('/push/key')
}

/** 登记这个浏览器的推送订阅。幂等：同一个 endpoint 再来一次就覆盖。 */
export function savePushSubscription(body: {
  endpoint: string
  p256dh: string
  auth: string
  user_agent?: string
}): Promise<{ saved: boolean }> {
  return request<{ saved: boolean }>('/push/subscriptions', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

/** 退订这个浏览器。走 POST 是因为 endpoint 是个上千字符的 URL，见后端那条注释。 */
export function dropPushSubscription(endpoint: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>('/push/subscriptions/delete', {
    method: 'POST',
    body: JSON.stringify({ endpoint }),
  })
}
