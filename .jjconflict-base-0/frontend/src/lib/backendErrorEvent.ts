/**
 * Backend error events in the chat stream (backend: app/domain/backend_log.py).
 *
 * These blocks exist for 芝士, who can read neither `docker logs` nor the host's
 * log file — but they land in a ROOM, which is a conversation and not a
 * monitoring dashboard. So the two readers get different amounts: the block's
 * `content` is already a single line, and the traceback stays folded in `meta`
 * until someone asks for it. 芝士 reads the whole block through the API either
 * way; folding costs it nothing.
 */
import type { Block } from '../cx_types'

export interface BackendErrorPresentation {
  /** The always-visible line — already one line, from the backend. */
  line: string
  /** Traceback, shown only once expanded. Empty when the report carried none. */
  stack: string
  /** "POST /api/topics/{id}/chat", when the report knew. */
  where: string
  /** Correlation id, for joining back to the log stream. */
  requestId: string
  /** >0 on a burst summary: how many occurrences this one line stands for. */
  count: number
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function backendErrorPresentation(block: Block): BackendErrorPresentation | null {
  const meta = block.meta
  if (!meta || meta.event_type !== 'backend_error') return null

  return {
    line: block.content,
    stack: str(meta.stack),
    where: str(meta.where),
    requestId: str(meta.request_id),
    count: meta.summary === true && typeof meta.count === 'number' ? meta.count : 0,
  }
}
