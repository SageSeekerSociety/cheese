// The AI teammate editing a room's document: changes asked for on a selection,
// and the edits a person makes to undo or restore one of its changes.
import type { DocEdit, DocRewriteRequest, DocRewriteResult } from '../lib/docEdits'

import { request } from '../api'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/doc`
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
export function applyDocEdits(topic: string, edits: DocEdit[]): Promise<DocEditsResult> {
  return request(`${root(topic)}/edits`, json({ edits }))
}

/** The suggestions waiting in the document as last stored, with the reason each was made. */
export async function getPendingSuggestions(topic: string): Promise<PendingSuggestionInfo[]> {
  const doc = await request<{ pending_suggestions?: PendingSuggestionInfo[] } | null>(root(topic))
  return doc?.pending_suggestions ?? []
}

/** Ask the room's AI teammate to rewrite the selected text; it edits the document itself. */
export function rewriteDocSelection(topic: string, body: DocRewriteRequest): Promise<DocRewriteResult> {
  return request(`${root(topic)}/rewrite`, json(body))
}
