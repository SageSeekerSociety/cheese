// 已读：谁读到了一段对话里的哪几条（后端 `routes/message_reads.py`）。
//
// 一段对话里每块都有一个编号（`Block.seq`）；读过的那几段编号按「读的人」给：
// 一个读的人一两段，不管这一页有多少条消息，频道里六十个人也只是六十来行。
// 某一条谁读过，由页面拿它的编号去对。
import { request } from './http'

/** 编号 `[first, last]`，两头都算。 */
export type SeqStretch = [number, number]

export interface ReaderReads {
  reader: string
  ranges: SeqStretch[]
}

/** 编号 `first..last` 之间，谁读过哪几段（人和 AI 队友都在里面）。 */
export function getMessageReads(
  conversationId: string,
  first: number,
  last: number
): Promise<{ readers: ReaderReads[] }> {
  return request<{ readers: ReaderReads[] }>(
    `/topics/${encodeURIComponent(conversationId)}/reads?first=${first}&last=${last}`
  )
}

/** 这几段是我的页面在前台时摆在屏幕上的：记成我读过。返回我变长了的那几段。 */
export function reportMessageReads(conversationId: string, ranges: SeqStretch[]): Promise<{ ranges: SeqStretch[] }> {
  return request<{ ranges: SeqStretch[] }>(`/topics/${encodeURIComponent(conversationId)}/reads`, {
    method: 'POST',
    body: JSON.stringify({ ranges }),
  })
}
