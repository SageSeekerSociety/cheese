// 任务交了一版去审阅时，在它出自的那段对话里留的那一行（后端
// `review.task_landing.tell_origin`，`meta.delivery`）：是哪件任务、交的是什么、第几版。
// 这一行只记交上去的那一刻，不写审阅到哪一步了，之后也不在原处变。
import type { Block } from '../cx_types'

export interface DeliveryLine {
  taskId: string
  cardId: string
  subject: string | null
  version: number
}

/** 这一块是不是一行交付，是的话交的是什么。 */
export function deliveryOf(block: Block): DeliveryLine | null {
  const meta = (block.meta ?? null) as Record<string, unknown> | null
  const delivery = meta?.delivery as Record<string, unknown> | undefined
  const taskId = meta?.task_result
  if (!delivery || typeof taskId !== 'string') return null
  const { card_id: cardId, subject, version } = delivery
  if (typeof cardId !== 'string' || typeof version !== 'number') return null
  return { taskId, cardId, subject: typeof subject === 'string' && subject ? subject : null, version }
}
