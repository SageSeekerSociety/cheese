// What the AI teammate is changing in the task's files, read off its room socket: the `live`
// frames carry a tool call's arguments as they are written (`types/live.ts`), and a `landed`
// frame says the call has run (backend `agent/landed_frames.py`).
//
// An edit is first only words the teammate is writing; the file on disk is unchanged until the
// call runs. So an edit here is `writing` while its arguments grow, still `writing` once they
// are whole and the call is running, and `landed` (or `failed`) when the landed frame names it.
//
// Only the teammate's own file tools are read: an edit that replaces one passage (`Edit`), and
// a write of a whole file (`Write`). A shell command that changes a file says nothing here.
import type { LiveBlock } from '../types/live'

import { partialStringField } from './partialJson'

export type FollowedState = 'writing' | 'landed' | 'failed'

export interface FollowedEdit {
  /** The tool call's id: the same in its `live` blocks and in the `landed` frame. */
  call: string
  agent: string
  /** The file as the teammate named it: an absolute path inside its environment, usually. */
  path: string
  /** The passage it replaces, once that is written whole; null for a whole-file write. */
  old: string | null
  /** The passage it is written to `old` and still being written. */
  oldPending: boolean
  /** What it puts there, as written so far. */
  text: string
  state: FollowedState
}

/** The file tools whose arguments say what will change, whatever prefix a harness gives them. */
function tool(name: string | null): 'edit' | 'write' | null {
  const bare = (name ?? '').replace(/^mcp__[a-z0-9_]+__/, '')
  if (bare === 'Edit') return 'edit'
  if (bare === 'Write') return 'write'
  return null
}

/** The edit one live block is writing, once it names its file; null for any other block. */
export function editOf(block: LiveBlock, agent: string): FollowedEdit | null {
  if (block.type !== 'tool' || !block.id) return null
  const kind = tool(block.name)
  if (!kind) return null
  const path = partialStringField(block.arguments, 'file_path')
  if (!path?.closed || !path.text) return null
  if (kind === 'write') {
    const content = partialStringField(block.arguments, 'content')
    return {
      call: block.id,
      agent,
      path: path.text,
      old: null,
      oldPending: false,
      text: content?.text ?? '',
      state: 'writing',
    }
  }
  const old = partialStringField(block.arguments, 'old_string')
  const next = partialStringField(block.arguments, 'new_string')
  return {
    call: block.id,
    agent,
    path: path.text,
    old: old?.closed ? old.text : null,
    oldPending: !old?.closed,
    text: next?.text ?? '',
    state: 'writing',
  }
}

/**
 * The task file an environment path names: the listed file whose path the named one ends with,
 * the longest such. A path already relative to the checkout is itself when listed. Null when no
 * listed file matches (outside the checkout, or not listed yet).
 */
export function checkoutPath(named: string, listed: readonly string[]): string | null {
  const clean = named.replace(/\\/g, '/')
  let best: string | null = null
  for (const path of listed) {
    if (clean !== path && !clean.endsWith(`/${path}`)) continue
    if (!best || path.length > best.length) best = path
  }
  return best
}
