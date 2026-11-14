// 任务交了一版去审阅时，平台在它出自的那段对话里记下的那一行（后端
// `review.task_landing.record_at_origin`，`event_type` 为 `task_filed`）：是哪件任务、
// 递的哪张卡、第几版。这一行只记交上去的那一刻，不写审阅到哪一步了，之后也不在原处变。
import type { Block } from '../cx_types'

import { renderNoticeMessage } from './noticeText'

export interface DeliveryLine {
  taskId: string
  cardId: string
  /** 记下时任务叫什么，按读者的语言（没起名的任务是那个占位词）。 */
  title: string
  version: number
}

/** 这一块是不是一行交付，是的话交的是什么。 */
export function deliveryOf(block: Block): DeliveryLine | null {
  if (block.kind !== 'event') return null
  const meta = (block.meta ?? null) as Record<string, unknown> | null
  if (meta?.event_type !== 'task_filed') return null
  const delivery = meta.delivery as Record<string, unknown> | undefined
  const taskId = meta.task_id
  if (!delivery || typeof taskId !== 'string') return null
  const { card_id: cardId, version } = delivery
  if (typeof cardId !== 'string' || typeof version !== 'number') return null
  return { taskId, cardId, title: titleOf(meta), version }
}

/** 句子参数里的任务名（`meta.i18n.content.params.title`）：一个名字，或占位词那一句。 */
function titleOf(meta: Record<string, unknown>): string {
  const content = (meta.i18n as { content?: { params?: Record<string, unknown> } } | undefined)?.content
  const title = content?.params?.title
  return typeof title === 'string' ? title : renderNoticeMessage(title, '')
}
