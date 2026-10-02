// The living document's way in: a ticket for the collaboration service, and
// where the browser reaches it.

import { request } from '../api'

// A short-lived ticket that opens this room's living document in the
// collaboration service. Whether it may be changed is decided by the backend and
// carried in the ticket; `read_only` says the same thing to the screen.
export interface DocTicket {
  document: string
  ticket: string
  read_only: boolean
}

export function getDocTicket(topicId: string): Promise<DocTicket> {
  return request<DocTicket>(`/topics/${encodeURIComponent(topicId)}/doc/ticket`)
}

// Where the browser reaches the collaboration service: the frontend's own
// /collab location (nginx in production, vite's proxy in development).
export function collabWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/collab`
}
