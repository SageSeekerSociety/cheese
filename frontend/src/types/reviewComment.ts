/** A comment on lines of a task's changes (`backend/app/domain/review/comments.py`).
 *
 * `line_start`/`line_end` number the lines in the version it was written on.
 * `current_line` is where those lines start in the version now under review:
 * the same for a draft, wherever the text moved to for a sent comment, null when
 * the lines are gone. */
export interface ReviewComment {
  id: string
  author: string
  path: string
  line_start: number
  line_end: number
  line_text: string
  current_line: number | null
  body: string
  /** A 修改建议: what the lines should read instead. */
  suggestion: string | null
  /** A reply sits under the sent comment it answers. */
  parent_id: string | null
  state: 'draft' | 'sent'
  card_id: string | null
  sent_at: string | null
  outcome: 'handled' | 'not_handled' | null
  outcome_note: string | null
  created_at: string
}

/** What writing a comment sends. A reply names its parent and takes its place. */
export interface ReviewCommentDraft {
  path: string
  line_start: number
  line_end: number
  line_text: string
  body: string
  suggestion: string | null
  parent_id?: string | null
}

/** A region of a merged save: text both sides agree on, or a conflict to pick. */
export type MergeRegion =
  | { kind: 'same'; text: string }
  | { kind: 'conflict'; base: string; mine: string; theirs: string }

/** A 409 from a save whose edits overlapped someone else's. */
export interface MergeConflict {
  regions: MergeRegion[]
  /** The version to save over, and the text it names. */
  version: string
  base: string
}

/** What the 改动 list needs to draw a task's comments. */
export interface DiffReview {
  /** The comments to show: the viewer's drafts and the last round's. */
  comments: ReviewComment[]
  /** The task awaits review, so comments can be written. */
  writable: boolean
  me: string
  busy: boolean
  agentName: string
}

/** The 改动 tab's comments: what to draw, and what writing one does. */
export interface ReviewBundle extends DiffReview {
  add: (draft: ReviewCommentDraft) => Promise<void>
  edit: (id: string, body: string, suggestion: string | null) => Promise<void>
  remove: (id: string) => Promise<void>
}
