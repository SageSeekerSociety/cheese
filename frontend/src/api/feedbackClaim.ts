// 领取一条反馈（`/feedback/{ref}/claim`）：记到自己名下、从此刻算「处理中」，别人再领
// 会被拒（409，说出是谁领着）。放弃是同一个地址的 DELETE。
import type { FeedbackDetail } from '@/cx_types'

import { request } from '../api'

/** 详情上服务端算好的两位：这个读者现在能不能领取、能不能放弃。按钮照它画，客户端
 *  不自己拼「我是不是持有人 / 管理员」——那是「按钮画得出来、点下去 403」的来源。
 *  持有人本身是卡片上的 `assignee_handle`。 */
export interface FeedbackClaimFlags {
  can_claim: boolean
  can_release: boolean
}

const claimUrl = (feedbackId: string) => `/feedback/${encodeURIComponent(feedbackId)}/claim`

export function claimFeedback(feedbackId: string): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(claimUrl(feedbackId), { method: 'POST' })
}

export function releaseFeedback(feedbackId: string): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(claimUrl(feedbackId), { method: 'DELETE' })
}
