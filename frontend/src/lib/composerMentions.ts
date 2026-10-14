import type { MentionMember, MentionTopic } from './expandMentions'

import { isAgentHandle } from './authorship'
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

const BROADCAST = new Set(['all', 'here'])

/** Whether `expanded` @-s someone and every one it @-s is a person: a message said to
 *  those people, which a task's teammate does not pick up although every other message
 *  there calls it. `@all`/`@here` take in the whole channel, the teammate included. */
export function namesOnlyPeople(expanded: string, seat: AgentSeat, pool: Member[]): boolean {
  const handles = [...expanded.matchAll(/<@([\w-]+)>/g)].map((m) => m[1])
  return (
    handles.length > 0 &&
    handles.every(
      (h) =>
        !BROADCAST.has(h) &&
        !isAgentHandle(h) &&
        h !== seat?.handle &&
        !pool.some((member) => member.agent && member.handle === h)
    )
  )
}

/** The visible @ used by both the composer and explicit preview questions. */
export function withAgentMention(text: string, seat: AgentSeat, pool: Member[], topics: MentionTopic[]): string {
  if (!seat || mentionsAgent(expandComposerMentions(text, seat, pool, topics), seat, pool)) return text
  return `@${seat.label} ${text}`
}
