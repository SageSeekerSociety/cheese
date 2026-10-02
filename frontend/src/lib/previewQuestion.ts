import type { ChatAttachment, Topic } from '../cx_types'
import type { MentionMember } from './expandMentions'
import type { QuotedContext } from './quotedContext'

import { expandComposerMentions } from './composerMentions'
import { frozenQuote } from './quotedContext'

export interface PreviewQuestion {
  topicId: string
  content: string
  intent: 'ask-agent'
  quotedContext: QuotedContext
}

/** True means accepted into the normal outbox, not delivered or answered. */
export type SubmitPreviewQuestion = (request: PreviewQuestion) => boolean

export function createQuestionSubmit(options: {
  topic: () => Topic | null
  agentSeat: () => { handle: string; label: string } | null
  mentionPool: () => (MentionMember & { agent?: boolean })[]
  topicList: () => Topic[]
  send: (content: string, summon: boolean, attachments?: ChatAttachment[], quotedContext?: QuotedContext) => boolean
}): SubmitPreviewQuestion {
  return (request) => {
    const seat = options.agentSeat()
    if (request.intent !== 'ask-agent' || options.topic()?.id !== request.topicId || !seat || !request.content.trim())
      return false
    const pool = options.mentionPool()
    const topics = options.topicList().filter((topic) => topic.kind !== 'root')
    const authored = expandComposerMentions(request.content, seat, pool, topics)
    const token = `<@${seat.handle}>`
    const content = authored.startsWith(token) ? authored : `${token} ${authored}`
    return options.send(content, true, undefined, frozenQuote(request.quotedContext))
  }
}
