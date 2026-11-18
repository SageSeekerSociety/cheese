import type { MentionMember, MentionTopic } from './expandMentions'

import { expandMentions, mentionsHandle, mentionSpans } from './expandMentions'

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
 * teammate named inside the sentence is being talked about ("照 @芝士 说的改") and
 * stays, for the same reason the avatar's toggle only removes the one @ it added.
 * An @ on a person at the front is a sentence addressed to them: it keeps its place,
 * but a teammate behind it is still someone the draft hands work to, so it goes too.
 *
 * The rest of the draft comes back exactly as typed: this walks the raw text, so a
 * hand-typed "@某人" further down neither turns into a chip nor moves, and a trailing
 * newline survives.
 *
 * `named` says the caller named the teammate ("交给芝士" does). Without a name, a
 * draft that already summons someone keeps them: the avatar does not rewrite a @ the
 * person put there themselves.
 */
export function handToMentions(
  text: string,
  seat: AgentSeat,
  mates: { handle: string }[],
  members: Member[],
  topics: MentionTopic[],
  named: boolean
): string {
  if (!seat) return text
  const summons = (handle: string) => mates.some((m) => m.handle === handle)
  if (!named) {
    if (mates.some((m) => mentionsHandle(expandComposerMentions(text, seat, members, topics), m.handle))) return text
    return `<@${seat.handle}> ${text}`
  }
  // 开头的空白连着 @：队友的拿走，人的留着（那句话是说给他听的）。
  const kept: string[] = []
  let cursor = 0
  for (;;) {
    const start = cursor + /^\s*/.exec(text.slice(cursor))![0].length
    const span = mentionSpans(text.slice(start), members, topics).find((s) => s.start === 0)
    if (!span) break
    const token = text.slice(start, start + span.end)
    const handle = /^<@([\w-]+)>$/.exec(expandComposerMentions(token, seat, members, topics))?.[1]
    if (handle && summons(handle)) {
      cursor = start + span.end
      continue
    }
    // 人的 @：原样留着，接着看他后面还有没有队友。
    kept.push(token)
    cursor = start + span.end
  }
  const rest = text.slice(cursor).trimStart()
  return `<@${seat.handle}> ${[...kept, rest].filter(Boolean).join(' ')}`
}
