// Asking the room's AI teammate from the document: one question (answered as a
// stream), stopping it, and putting its answer into a comment thread.
import type { DocAgentRequest } from '../lib/docAgent'

import { request } from '../api'

import { postEventStream } from './eventStream'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/doc/agent`

export function askDocAgent(
  topic: string,
  body: DocAgentRequest,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal
): Promise<void> {
  return postEventStream(root(topic), body, onEvent, { signal })
}

export function stopDocAgent(topic: string, conversation: string): Promise<unknown> {
  return request(`${root(topic)}/${encodeURIComponent(conversation)}/stop`, { method: 'POST' })
}

/** The conversation's last answer, as the teammate's reply in the thread `thread`. */
export function replyWithAnswer(topic: string, conversation: string, thread: string): Promise<unknown> {
  return request(`${root(topic)}/${encodeURIComponent(conversation)}/reply/${encodeURIComponent(thread)}`, {
    method: 'POST',
  })
}
