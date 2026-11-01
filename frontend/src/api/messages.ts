// 在房间里说一句话。
//
// 拆在 `api.ts` 外面：那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），只能
// 变短。人和 AI 队友走的是同一扇门：后端按发送者在这个房间里的席位决定这是一个人说
// 的话（点到谁就叫醒谁）还是一位队友的发言。房间的 socket 只负责把落下的东西推给所
// 有在看的人。
import type { Block, ChatMessageBody } from '../cx_types'

import { request } from '../api'

import { changesAwaiting } from './awaitingChanged'

/** 这条消息 @ 到、却不在这个频道里的人。只回给发的人，不落库。 */
export interface MentionedOutside {
  /** 频道的 id（消息发在任务或支线里时也是它所在的频道）。 */
  room_id: string
  handles: string[]
  /** 发的人能不能把他们加进来。 */
  may_add: boolean
  /** 私密频道：不在里面的人看不到这条消息。 */
  members_only: boolean
}

export type PostedMessage = Block & {
  mentioned_outside?: MentionedOutside
  /** 这条没交给任何 AI 队友，而这个频道里只有发的人自己。只回给发的人，不落库。 */
  alone?: boolean
}

/** 一条消息此刻发出去会交给谁：后端照发送时的同一套规矩读草稿，不写任何东西。 */
export interface Addressing {
  /** 会为它开一轮的 AI 队友（席位和名字）；不交给任何一位时是 null。 */
  agent: { handle: string; name: string } | null
  /** 哪条规矩交给了它：@ 了它、回复它的消息、任务负责人在开着的任务里说、和它私聊。 */
  reason: 'mention' | 'reply' | 'task' | 'private' | null
  /** @ 到的人。 */
  people: string[]
  place: 'channel' | 'task' | 'private'
  task_status: 'open' | 'closed' | null
}

export interface AddressingDraft {
  content: string
  reply_to?: string
  /** 只发附件、没有正文时是 false：那样的一条 @ 和回复都不叫谁。 */
  with_text?: boolean
}

export function previewAddressing(topicId: string, draft: AddressingDraft, signal?: AbortSignal): Promise<Addressing> {
  return request<Addressing>(`/topics/${encodeURIComponent(topicId)}/messages/addressing`, {
    method: 'POST',
    body: JSON.stringify(draft),
    signal,
  })
}

/** 返回落库的那一条；同一个 `request_id` 重发只会得到同一条。 */
export function postChatMessage(topicId: string, body: ChatMessageBody, signal?: AbortSignal): Promise<PostedMessage> {
  return changesAwaiting(
    request<PostedMessage>(`/topics/${encodeURIComponent(topicId)}/messages`, {
      method: 'POST',
      body: JSON.stringify(body),
      signal,
    })
  )
}
