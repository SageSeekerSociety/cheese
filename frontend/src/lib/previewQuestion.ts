import type { Topic } from '../cx_types'
import type { MentionMember } from './expandMentions'

import { expandComposerMentions, withAgentMention } from './composerMentions'

export interface PreviewQuestion {
  topicId: string
  content: string
  intent: 'ask-agent'
}

/** True means accepted into the normal outbox, not delivered or answered. */
export type SubmitPreviewQuestion = (request: PreviewQuestion) => boolean

export function createQuestionSubmit(options: {
  topic: () => Topic | null
  agentSeat: () => { handle: string; label: string } | null
  mentionPool: () => (MentionMember & { agent?: boolean })[]
  topicList: () => Topic[]
  send: (content: string, summon: boolean) => boolean
}): SubmitPreviewQuestion {
  return (request) => {
    const seat = options.agentSeat()
    if (request.intent !== 'ask-agent' || options.topic()?.id !== request.topicId || !seat || !request.content.trim())
      return false
    const pool = options.mentionPool()
    const topics = options.topicList().filter((topic) => topic.kind !== 'root')
    const content = expandComposerMentions(withAgentMention(request.content, seat, pool, topics), seat, pool, topics)
    return options.send(content, true)
  }
}
