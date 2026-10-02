import type { Block, ChatMessageBody } from '../cx_types'
import type { Outgoing } from './composerDrafts'

export function outgoingMessageBody(item: Outgoing): ChatMessageBody {
  const body: ChatMessageBody = {
    content: item.content,
    request_id: item.clientId,
    reply_to: item.replyTo,
    attachments: item.atts,
    quoted_context: item.quotedContext,
  }
  return body
}

/** Pending rows carry the same quote as the HTTP message they will become. */
export function pendingMessageBlock(item: Outgoing, author: string): Block {
  return {
    id: item.clientId,
    author,
    content: item.content,
    kind: 'message',
    meta: item.quotedContext ? { quoted_context: item.quotedContext } : undefined,
  } as Block
}
