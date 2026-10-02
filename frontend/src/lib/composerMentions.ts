import type { MentionMember, MentionTopic } from './expandMentions'

import { expandMentions, mentionsHandle } from './expandMentions'

type AgentSeat = { handle: string; label: string } | null
type Member = MentionMember & { agent?: boolean }

export function expandComposerMentions(text: string, seat: AgentSeat, pool: Member[], topics: MentionTopic[]): string {
  return expandMentions(text, seat ? [seat, ...pool] : pool, topics)
}

export function mentionsAgent(text: string, seat: AgentSeat, pool: Member[]): boolean {
  if (mentionsHandle(text, seat?.handle)) return true
  if (!seat) return false
  return pool.some((member) => member.agent && mentionsHandle(text, member.handle))
}

/** The visible @ used by both the composer and explicit preview questions. */
export function withAgentMention(text: string, seat: AgentSeat, pool: Member[], topics: MentionTopic[]): string {
  if (!seat || mentionsAgent(expandComposerMentions(text, seat, pool, topics), seat, pool)) return text
  return `@${seat.label} ${text}`
}
