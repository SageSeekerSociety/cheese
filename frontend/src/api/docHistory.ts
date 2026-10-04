// A room document's versions: the newest first, a page at a time, and putting
// an earlier one back.
import type { DocVersionPage } from '../lib/docHistory'

import { request } from '../api'
import { shareInFlight } from '../lib/inflight'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/doc`

export function getDocVersions(topic: string, { before, limit }: { before?: number; limit?: number } = {}) {
  const query = new URLSearchParams({ newest: 'true' })
  if (before !== undefined) query.set('before', String(before))
  if (limit !== undefined) query.set('limit', String(limit))
  // 打开话题时会读两次这一版历史（「最近一次编辑」读 limit:1；「改动」那一格同时
  // 读整页），两次指向同一个资源 —— 在飞的那条共享给后来的人。键里带 query，
  // limit:1 和整页是两条不同的请求，不会互相顶掉。
  const path = `${root(topic)}/history?${query}`
  return shareInFlight(`docVersions:${path}`, () => request<DocVersionPage>(path))
}

/** Make `version` the document's text again, as a new version on top of `expected`. */
export function restoreDocVersion(topic: string, version: number, expected: number): Promise<unknown> {
  return request(`${root(topic)}/restore`, {
    method: 'POST',
    body: JSON.stringify({ version, expected_version: expected, operation_id: crypto.randomUUID() }),
  })
}
