// A checklist message of the caller's own — the same write an agent's
// `todo_write` makes. `new` posts another list; `message` edits that list of
// theirs; with neither, their newest list is edited (or the first one posted).
// Every call carries the whole list.
import type { TodoItem } from '../cx_types'

import { request } from '../api'

export function writeChecklist(
  topicId: string,
  todos: { content: string; status: TodoItem['status'] }[],
  opts: { new?: boolean; message?: string } = {}
): Promise<{ items: TodoItem[]; message_id: string; posted: boolean }> {
  return request(`/topics/${encodeURIComponent(topicId)}/progress`, {
    method: 'PUT',
    body: JSON.stringify({ todos, ...opts }),
  })
}
