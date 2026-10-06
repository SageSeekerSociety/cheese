// A document's way in: which document a task's is, a ticket for the
// collaboration service, and where the browser reaches it.

import { request } from '../api'
import { DOC_SCHEMA_PARAM, DOC_SCHEMA_VERSION } from '../lib/docSchema/version'

/** The task's living document: made, empty, the first time anyone asks. */
export function getRoomDocument(topicId: string): Promise<{ id: string }> {
  return request<{ id: string }>(`/topics/${encodeURIComponent(topicId)}/document`)
}

// A short-lived ticket that opens a document in the collaboration service. Whether it may be changed is decided by the backend and
// carried in the ticket; `read_only` says the same thing to the screen.
export interface DocTicket {
  document: string
  ticket: string
  read_only: boolean
}

export function getDocTicket(documentId: string): Promise<DocTicket> {
  return request<DocTicket>(`/documents/${encodeURIComponent(documentId)}/ticket`)
}

// Where the browser reaches the collaboration service: the frontend's own
// /collab location (nginx in production, vite's proxy in development). It says
// which document schema this build speaks: the service refuses another one.
export function collabWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/collab/?${DOC_SCHEMA_PARAM}=${DOC_SCHEMA_VERSION}`
}
