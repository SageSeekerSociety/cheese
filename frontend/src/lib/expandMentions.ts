/** Friendly "@名字 / @handle / @话题名" → the canonical token (<@handle> /
 * <#topicId>), for the human composer.
 *
 * The backend runs the same rewrite as a backstop (app/domain/mentions.py), so
 * a name typed without picking from the menu still resolves. This copy exists
 * because the composer needs the answer *before* sending: whether a draft
 * summons 芝士 is read off the expanded text, and that decision lives in the
 * input box.
 *
 * Two copies of one rewrite is two chances to disagree, and the disagreement is
 * silent — whatever the composer produces, the backend accepts as already
 * canonical. So the three guards below are the backend's, one for one.
 */

export interface MentionMember {
  handle: string
  /** Display name; falls back to the handle when absent. */
  label?: string | null
}

export interface MentionTopic {
  id: string
  title: string
}

// After an ASCII-word-ending @name/@handle, the next char must not continue the
// word — so roster handle "andy" never eats the front of "@andylizf".
// ASCII-only on purpose: JS \w and CJK aside, "@张衡来负责" must still resolve
// 张衡 even though 来 follows without a space.
const ASCII_WORD_END = /[A-Za-z0-9_-]$/
const ASCII_BOUNDARY = '(?![A-Za-z0-9_-])'

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function expandMentions(text: string, members: MentionMember[], topics: MentionTopic[] = []): string {
  // 群播 (fusion-design §3): @all/@here are fixed-literal tokens, not roster rows.
  const subs: [string, string][] = [
    ['@all', '<@all>'],
    ['@here', '<@here>'],
  ]
  for (const m of members) {
    const tok = `<@${m.handle}>`
    for (const key of [m.label, m.handle]) {
      if (key) subs.push([`@${key}`, tok]) // an empty pattern ("@") would swallow every @
    }
  }
  for (const t of topics) {
    if (t.title) subs.push([`@${t.title}`, `<#${t.id}>`])
  }
  subs.sort((a, b) => b[0].length - a[0].length) // longest first
  const seen = new Set<string>()
  let out = text
  for (const [pat, tok] of subs) {
    if (seen.has(pat)) continue // name === handle yields the same pattern twice,
    seen.add(pat) // and the second pass would re-wrap the token the first produced
    const boundary = ASCII_WORD_END.test(pat) ? ASCII_BOUNDARY : ''
    // (?<!<) keeps already-encoded tokens intact: the "@handle" inside a
    // produced "<@handle>" must not be re-wrapped by a shorter later pattern.
    // The replacement is a function so a "$" in a name can't act as $1.
    // Case-insensitive: "@Alice" and a mis-cased handle both map to the
    // canonical lowercase token, matching the backend.
    out = out.replace(new RegExp('(?<!<)' + escapeRe(pat) + boundary, 'gi'), () => tok)
  }
  return out
}

/**
 * 展开之后的正文里点没点到这个 handle。
 *
 * 房间和输入区都要问这件事：输入区拿它判「这条草稿算不算叫了它」，房间拿它判
 * 「最后那句话没叫它、该不该提示一下」。各留一份的话，总有一天两边对「算不算叫
 * 了」给出不同的答案，而那正是这套写法要消灭的东西。
 */
export function mentionsHandle(expanded: string, handle: string | undefined): boolean {
  return !!handle && expanded.includes(`<@${handle}>`)
}
