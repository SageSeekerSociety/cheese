// 在房间里说一句话。
//
// 拆在 `api.ts` 外面：那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），只能
// 变短。人和 AI 队友走的是同一扇门：后端按发送者在这个房间里的席位决定这是一个人说
// 的话（点到谁就叫醒谁）还是一位队友的发言。房间的 socket 只负责把落下的东西推给所
// 有在看的人。
import type { Block, ChatMessageBody } from '../cx_types'

import { request } from '../api'

/** 返回落库的那一条；同一个 `request_id` 重发只会得到同一条。 */
export function postChatMessage(topicId: string, body: ChatMessageBody, signal?: AbortSignal): Promise<Block> {
  return request<Block>(`/topics/${encodeURIComponent(topicId)}/messages`, {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  })
}
