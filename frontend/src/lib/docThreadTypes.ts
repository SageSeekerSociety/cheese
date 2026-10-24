import type { QuotedContext } from './quotedContext'

/** Where a room file's thread points, as stored: one of the quoting shapes. */
export type QuotedContextLike = QuotedContext | Record<string, unknown>

/** The room's agent answering a thread now: waiting for a free session, or answering. */
export type DocThreadAnswering = 'queued' | 'working'

/** A comment on a document: one that opens a thread, or a reply in one. */
export interface DocComment {
  id: string
  author: string
  content: string
  /** The words an opening comment is about, as they read when it was written. */
  anchor_quote: string | null
  /** Where a thread on a room file points — a place in it, and the version it
   *  was read at. A document's thread has none: its own text carries the mark. */
  anchor?: QuotedContextLike | null
  created_at: string
  /** The people its @s told. A name it @s is drawn as a mention only for them. */
  notified?: string[]
}

/** The people a comment just written @s who cannot open the document, said to
 *  its writer alone and never stored (the same shape a message's is). */
export interface CommentOutside {
  room_id: string
  handles: string[]
  may_add: boolean
  members_only: boolean
}

/** What a comment or a reply comes back as to the person who wrote it. */
export interface WrittenToWriter {
  mentioned_outside?: CommentOutside
}

export interface DocThread {
  /** The thread's first comment; `anchor_quote` is the words it is about. */
  comment: DocComment
  revision: number
  state: 'open' | 'resolved'
  replies: { sequence: number; comment: DocComment }[]
  /** Only in the thread list. */
  answering?: DocThreadAnswering | null
}
export interface DocThreadWrite {
  operation_id: string
  expected_revision: number
  content?: string
}
/** How far the agent has got with a thread's question, as the room hears it. */
export interface DocThreadActivity {
  state: DocThreadAnswering
  /** The tool it is using, when it said. */
  tool?: string
}
export interface DocThreadActions {
  reply: (id: string, content: string) => Promise<(DocThread & WrittenToWriter) | void>
  resolve: (id: string) => Promise<void>
  reopen: (id: string) => Promise<void>
  /** Send again the write whose outcome is unknown, or read the threads again. */
  recover: (id: string) => Promise<{ reply: string } | undefined>
  /** Stop the agent answering the thread; its reply keeps what it wrote. */
  stopAgent: (id: string) => Promise<void>
}
export interface DocThreadState {
  /** Every thread on the document, oldest first. */
  threads: DocThread[]
  activity: Record<string, DocThreadActivity>
  errors: Record<string, string>
  busy: boolean
  /** The thread whose last write may or may not have landed. */
  unknown: string | null
}
/** Where a thread's words are in the text: still there (marked), rewritten or
 *  deleted but with the place they were remembered, or nowhere to be found. */
export type ThreadPlace = 'marked' | 'placed' | null
