import type { DocThread, DocThreadSummary, DocThreadWrite } from '../lib/docThreadTypes'

import { request } from '../api'

export type * from '../lib/docThreadTypes'
const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/comments`
export function listDocThreads(
  topic: string,
  offset = 0,
  limit = 50
): Promise<{ data: DocThreadSummary[]; total: number }> {
  return request(`${root(topic)}/threads?offset=${offset}&limit=${limit}`)
}
export function getDocThread(topic: string, id: string): Promise<DocThread> {
  return request(`${root(topic)}/${encodeURIComponent(id)}/thread`)
}
export function writeDocThread(
  topic: string,
  id: string,
  action: 'replies' | 'resolve' | 'reopen',
  body: DocThreadWrite
): Promise<DocThread> {
  return request(`${root(topic)}/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: JSON.stringify(body) })
}
