// Asking the room's AI teammate from the document: one question (answered as a
// stream), stopping it, and putting its answer into a comment thread.
import type { DocAgentRequest } from '../lib/docAgent'

import { request } from '../api'

import { followEventStream, postEventStream } from './eventStream'

const root = (topic: string) => `/topics/${encodeURIComponent(topic)}/doc/agent`

/** Ask, and read the answer to its end: a stream that breaks before it is read
 *  on from where it broke, in the box's conversation. */
export function askDocAgent(
  topic: string,
  body: DocAgentRequest,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal
): Promise<void> {
  let conversation = body.conversation ?? ''
  return followEventStream(
    (seen) =>
      postEventStream(
        root(topic),
        body,
        (event, data, id) => {
          if (event === 'conversation' && typeof data.id === 'string') conversation = data.id
          seen(event, data, id)
        },
        { signal }
      ),
    (question, after) =>
      `${root(topic)}/${encodeURIComponent(conversation)}/answers/${encodeURIComponent(question)}?after=${encodeURIComponent(after)}`,
    onEvent,
    { signal }
  )
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
