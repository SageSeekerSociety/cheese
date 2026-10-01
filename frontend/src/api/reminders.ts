// 「到点提醒我」：请平台在 `at` 那个时刻把 `content` 递给自己。收件人永远是调用者，
// 由后端认出来，所以这里没有「发给谁」。AI 队友的 `cheese_deliver_at` 走的是同一个
// 接口。
import { request } from '../api'

export interface Reminder {
  id: string
  at: string
  to: string
}

export function setReminder(topicId: string, at: Date, content: string): Promise<Reminder> {
  return request<Reminder>(`/topics/${encodeURIComponent(topicId)}/deliveries`, {
    method: 'POST',
    body: JSON.stringify({ at: at.toISOString(), content }),
  })
}
