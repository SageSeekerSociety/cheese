// Backend `/api/topics/{id}/title` rejects blank titles but silently
// truncates over-length ones (see topics.py: `title[:80]`) rather than
// erroring — so the frontend enforces the same 80-char cap itself to avoid
// a save that visibly differs from what the user typed.
export const TOPIC_TITLE_MAX_LENGTH = 80

// Normalizes a draft rename: trims and caps length, then returns the title
// to save, or null if there's nothing worth saving (blank, or unchanged
// from the current title).
export function normalizeTopicTitle(
  draft: string,
  currentTitle: string,
  maxLength: number = TOPIC_TITLE_MAX_LENGTH
): string | null {
  const title = draft.trim().slice(0, maxLength)
  if (!title || title === currentTitle) return null
  return title
}
