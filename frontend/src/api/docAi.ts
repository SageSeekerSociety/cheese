import type {
  DocAiAccept,
  DocAiAccepted,
  DocAiInput,
  DocAiProposal,
  DocAiReceipt,
  DocAiRequest,
  DocAiSource,
} from '../lib/docAiTypes'

import { request } from '../api'

export type * from '../lib/docAiTypes'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/doc-ai`
const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export function getDocAiSource(topic: string, signal?: AbortSignal): Promise<DocAiSource> {
  return request(`${root(topic)}/source`, { signal })
}
export function createDocAiRequest(topic: string, input: DocAiInput): Promise<DocAiReceipt> {
  return request(`${root(topic)}/requests`, json(input))
}
export function listDocAiRequests(
  topic: string,
  signal?: AbortSignal
): Promise<{
  requests: (DocAiReceipt & { kind: 'ask' | 'propose'; created_at: string })[]
}> {
  return request(`${root(topic)}/requests`, { signal })
}
export function getDocAiRequest(topic: string, id: string, signal?: AbortSignal): Promise<DocAiRequest> {
  return request(`${root(topic)}/requests/${encodeURIComponent(id)}`, { signal })
}
export function cancelDocAiRequest(topic: string, id: string): Promise<DocAiReceipt> {
  return request(`${root(topic)}/requests/${encodeURIComponent(id)}/cancel`, json({}))
}
export function getDocAiProposal(topic: string, id: string, signal?: AbortSignal): Promise<DocAiProposal> {
  return request(`${root(topic)}/proposals/${encodeURIComponent(id)}`, { signal })
}
export function acceptDocAiProposal(topic: string, id: string, body: DocAiAccept): Promise<DocAiAccepted> {
  return request(`${root(topic)}/proposals/${encodeURIComponent(id)}/accept`, json(body))
}
