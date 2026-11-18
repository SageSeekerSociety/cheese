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

/** The visible @ the composer writes to hand a draft to the teammate: its reference, drawn as a chip. */
export function withAgentMention(text: string, seat: AgentSeat, pool: Member[], topics: MentionTopic[]): string {
  if (!seat || mentionsAgent(expandComposerMentions(text, seat, pool, topics), seat, pool)) return text
  return `<@${seat.handle}> ${text}`
}

/** Hand the draft to `seat`, replacing whoever the draft was already addressed to.
 *
 * Only the mentions IN FRONT are swapped: that run is the sentence's "to whom". A
 * teammate named inside the sentence is being talked about ("照 @芝士 说的改"), and
 * an earlier @ on a person is a sentence addressed to them — both stay, for the same
 * reason the avatar's toggle only removes the one @ it added.
 *
 * `named` says the caller named the teammate ("交给芝士" does). Without a name, a
 * draft that already summons someone keeps them: the avatar does not rewrite a @ the
 * person put there themselves.
 */
export function handToMentions(
  text: string,
  seat: AgentSeat,
  mates: { handle: string }[],
  expand: (text: string) => string,
  named: boolean
): string {
  if (!seat) return text
  const expanded = expand(text)
  const summons = (handle: string) => mates.some((m) => m.handle === handle)
  if (!named) {
    if (mates.some((m) => expanded.includes(`<@${m.handle}>`))) return text
    return `<@${seat.handle}> ${text}`
  }
  let rest = expanded
  for (let at = /^<@([\w-]+)>\s*/.exec(rest); at && summons(at[1]); at = /^<@([\w-]+)>\s*/.exec(rest)) {
    rest = rest.slice(at[0].length)
  }
  return `<@${seat.handle}> ${rest}`.trimEnd()
}
