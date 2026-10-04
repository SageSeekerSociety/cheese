// Changing passages of a room's document as a person: undoing or restoring a
// change the AI teammate made, and the suggestions waiting to be decided.
import type { DocEdit } from '../lib/docEdits'

import { request } from '../api'

const root = (document: string) => `/documents/${encodeURIComponent(document)}`
const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export interface DocEditsResult {
  mode: 'direct' | 'suggest'
  requested_by: string | null
  edits: (DocEdit & { suggestion_id?: string })[]
  doc_version: number
}

export interface PendingSuggestionInfo {
  id: string
  author: string
  old: string
  new: string
  reason: string | null
}

/** Replace text in the live document; each `old` must occur exactly once. */
export function applyDocEdits(document: string, edits: DocEdit[]): Promise<DocEditsResult> {
  return request(`${root(document)}/edits`, json({ edits }))
}

/** The suggestions waiting in the document as last stored, with the reason each was made. */
export async function getPendingSuggestions(document: string): Promise<PendingSuggestionInfo[]> {
  const doc = await request<{ pending_suggestions?: PendingSuggestionInfo[] } | null>(root(document))
  return doc?.pending_suggestions ?? []
}
