// The AI teammate editing a room's document: changes asked for on a selection,
// and the edits a person makes to undo or restore one of its changes.
import type { DocEdit, DocRewriteRequest, DocRewriteResult } from '../lib/docEdits'

import { request } from '../api'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/doc`
const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export interface DocEditsResult {
  stored: boolean
  edits: (DocEdit & { suggestion_id?: string })[]
}

/** Replace text in the live document; each `old` must occur exactly once. */
export function applyDocEdits(topic: string, edits: DocEdit[]): Promise<DocEditsResult> {
  return request(`${root(topic)}/edits`, json({ edits }))
}

/** Ask the room's AI teammate to rewrite the selected text; it edits the document itself. */
export function rewriteDocSelection(topic: string, body: DocRewriteRequest): Promise<DocRewriteResult> {
  return request(`${root(topic)}/rewrite`, json(body))
}
