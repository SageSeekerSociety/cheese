import type { Block } from '../cx_types'

/** The room's agent answering a thread now: waiting for a free session, or answering. */
export type DocThreadAnswering = 'queued' | 'working'

export interface DocThread {
  /** The thread's first comment; `anchor_quote` is the words it is about. */
  comment: Block
  revision: number
  state: 'open' | 'resolved'
  replies: { sequence: number; comment: Block }[]
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
  reply: (id: string, content: string) => Promise<void>
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
