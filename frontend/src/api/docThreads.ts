import type { DocThread, DocThreadWrite } from '../lib/docThreadTypes'

import { request } from '../api'

export type * from '../lib/docThreadTypes'
const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/comments`
/** Every thread on the room's document with its replies. */
export function listDocThreads(topic: string): Promise<{ data: DocThread[]; total: number }> {
  return request(`${root(topic)}/threads`)
}
export function writeDocThread(
  topic: string,
  id: string,
  action: 'replies' | 'resolve' | 'reopen',
  body: DocThreadWrite
): Promise<DocThread> {
  return request(`${root(topic)}/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: JSON.stringify(body) })
}
/** Stop the agent answering the thread: its wait, or the reply being written. */
export function stopDocThreadAgent(topic: string, id: string): Promise<unknown> {
  return request(`${root(topic)}/${encodeURIComponent(id)}/agent/stop`, { method: 'POST' })
}
