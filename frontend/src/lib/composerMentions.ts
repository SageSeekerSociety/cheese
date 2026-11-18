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
  // 认名字用的是发送那一刻同一份名单（座位在最前）：手打的「@芝士」也认得出是它，
  // 不然这一下写一个、发的时候再展开一个，同一条里就点了它两次。
  const roster = seat ? [seat, ...members] : members
  const summons = (handle: string) => mates.some((m) => m.handle === handle)
  const expanded = expandMentions(text, roster, topics)
  if (!named) {
    if (mates.some((m) => mentionsHandle(expanded, m.handle))) return text
    return `<@${seat.handle}> ${text}`
  }
  // 只看开头那一段：跳过空白，看这里是不是一个 @，是队友的连它一起拿掉，是人的留着
  // （那句话是说给他听的），接着看它后面还有没有队友。拿完之后剩下的正文逐字交回。
  const kept: string[] = []
  let cursor = 0
  for (;;) {
    const start = cursor + /^\s*/.exec(text.slice(cursor))![0].length
    const span = mentionSpans(text.slice(start), roster, topics).find((s) => s.start === 0)
    if (!span) break
    const at = start + span.end
    const handle = /^<@([\w-]+)>$/.exec(expandMentions(text.slice(start, at), roster, topics))?.[1]
    if (!handle || !summons(handle)) kept.push(text.slice(start, at))
    cursor = at
  }
  const body = text.slice(cursor)
  const head = kept.join(' ')
  const joined = head ? head + body : body.trimStart()
  return joined ? `<@${seat.handle}> ${joined}` : `<@${seat.handle}>`
}
