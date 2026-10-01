import type { Block } from '../cx_types'

export interface DocThreadSummary {
  comment: Block
  revision: number
  state: 'open' | 'resolved'
  // Stored history; never reinterpret quote as a current writable selection.
  anchor: Record<string, unknown> | null
  reply_count: number
}
export interface DocThread {
  comment: Block
  revision: number
  state: 'open' | 'resolved'
  anchor: Record<string, unknown> | null
  replies: { sequence: number; comment: Block }[]
}
export interface DocThreadWrite {
  operation_id: string
  expected_revision: number
  content?: string
}
export interface DocThreadActions {
  load: (id: string) => Promise<void>
  reply: (id: string, content: string) => Promise<void>
  resolve: (id: string) => Promise<void>
  reopen: (id: string) => Promise<void>
  recover: (id: string) => Promise<void>
}
export interface DocThreadState {
  threads: Record<string, DocThread>
  errors: Record<string, string>
  busy: boolean
  unknown: string | null
}
