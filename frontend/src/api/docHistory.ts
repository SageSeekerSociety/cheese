// A room document's versions: the newest first, a page at a time, and putting
// an earlier one back.
import type { DocVersionPage } from '../lib/docHistory'

import { request } from '../api'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/doc`

export function getDocVersions(topic: string, { before, limit }: { before?: number; limit?: number } = {}) {
  const query = new URLSearchParams({ newest: 'true' })
  if (before !== undefined) query.set('before', String(before))
  if (limit !== undefined) query.set('limit', String(limit))
  return request<DocVersionPage>(`${root(topic)}/history?${query}`)
}

/** Make `version` the document's text again, as a new version on top of `expected`. */
export function restoreDocVersion(topic: string, version: number, expected: number): Promise<unknown> {
  return request(`${root(topic)}/restore`, {
    method: 'POST',
    body: JSON.stringify({ version, expected_version: expected, operation_id: crypto.randomUUID() }),
  })
}
