import type { DocThread, DocThreadWrite, QuotedContextLike, WrittenToWriter } from '../lib/docThreadTypes'

import { request } from '../api'

export type * from '../lib/docThreadTypes'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/files/comments`
const withPath = (path: string) => `?path=${encodeURIComponent(path)}`

/** Every thread on this file of the room, with its replies, oldest first. */
export function listFileThreads(topic: string, path: string): Promise<{ data: DocThread[]; total: number }> {
  return request(`${root(topic)}/threads${withPath(path)}`)
}

/** Start a thread on the file: where it points (``anchor``), how that reads
 *  (``quote``), and what it says. */
export function postFileThread(
  topic: string,
  body: { path: string; content: string; quote: string | null; anchor: QuotedContextLike | null }
): Promise<DocThread['comment']> {
  return request(root(topic), { method: 'POST', body: JSON.stringify(body) })
}

/** Stop the agent answering the thread: its wait, or the reply being written. */
export function stopFileThreadAgent(topic: string, path: string, id: string): Promise<unknown> {
  return request(`${root(topic)}/${encodeURIComponent(id)}/agent/stop${withPath(path)}`, { method: 'POST' })
}

export function writeFileThread(
  topic: string,
  path: string,
  id: string,
  action: 'replies' | 'resolve' | 'reopen',
  body: DocThreadWrite
): Promise<DocThread & WrittenToWriter> {
  return request(`${root(topic)}/${encodeURIComponent(id)}/${action}${withPath(path)}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
