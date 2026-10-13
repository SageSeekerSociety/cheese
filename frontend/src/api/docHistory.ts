// A document's versions: the newest first, a page at a time, and putting
// an earlier one back.
import type { DocVersionPage } from '../lib/docHistory'

import { request } from '../api'

const root = (document: string) => `/documents/${encodeURIComponent(document)}`

export function getDocVersions(document: string, { before, limit }: { before?: number; limit?: number } = {}) {
  const query = new URLSearchParams({ newest: 'true' })
  if (before !== undefined) query.set('before', String(before))
  if (limit !== undefined) query.set('limit', String(limit))
  return request<DocVersionPage>(`${root(document)}/history?${query}`)
}

/** Make `version` the document's text again, as a new version on top of `expected`. */
export function restoreDocVersion(document: string, version: number, expected: number): Promise<unknown> {
  return request(`${root(document)}/restore`, {
    method: 'POST',
    body: JSON.stringify({ version, expected_version: expected, operation_id: crypto.randomUUID() }),
  })
}
