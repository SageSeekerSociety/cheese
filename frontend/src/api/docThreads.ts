import type { DocThread, DocThreadWrite } from '../lib/docThreadTypes'

import { request } from '../api'

export type * from '../lib/docThreadTypes'
const root = (document: string) => `/documents/${encodeURIComponent(document)}/comments`
/** Every thread on the document with its replies. */
export function listDocThreads(document: string): Promise<{ data: DocThread[]; total: number }> {
  return request(`${root(document)}/threads`)
}
export function writeDocThread(
  document: string,
  id: string,
  action: 'replies' | 'resolve' | 'reopen',
  body: DocThreadWrite
): Promise<DocThread> {
  return request(`${root(document)}/${encodeURIComponent(id)}/${action}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
/** Stop the agent answering the thread: its wait, or the reply being written. */
export function stopDocThreadAgent(document: string, id: string): Promise<unknown> {
  return request(`${root(document)}/${encodeURIComponent(id)}/agent/stop`, { method: 'POST' })
}
