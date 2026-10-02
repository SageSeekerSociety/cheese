// What an agent is in the middle of writing, on its room's socket
// (backend `app/domain/agent/live_frames.py`). Nothing stores it and nothing
// replays it: a client that missed a frame has lost nothing, because the next
// one, or the message that lands when the writing is done, says all of it.

/** One block the agent is generating: text, or a tool call whose arguments are the raw JSON streamed so far. */
export type LiveBlock =
  | { type: 'text'; text: string }
  | { type: 'tool'; id: string | null; name: string | null; arguments: string }

/** `blocks` empty: the agent is writing nothing at the moment. */
export interface LiveFrame {
  type: 'live'
  turn_id: string | null
  /** The seat writing it: the handle its messages are signed with. */
  agent: string
  blocks: LiveBlock[]
}
