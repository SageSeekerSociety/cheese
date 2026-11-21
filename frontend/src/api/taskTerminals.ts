// A person's terminals in a task (backend `topics_task_terminals`): which of their shells are
// there, and closing one. Typing into one goes over its own socket (`taskTerminalWsUrl`).
import { request } from './http'

export interface TaskTerminal {
  id: string
  /** The shell exited; it stays listed until it is closed. */
  ended: boolean
}

/** Why the person may not open a terminal in the task. */
export type TaskTerminalRefusal = 'not_owner' | 'unsupported' | 'outdated'

export interface TaskTerminals {
  terminals: TaskTerminal[]
  refused: TaskTerminalRefusal | null
}

/** The caller's shells in the task's environment, oldest first. */
export function listTaskTerminals(taskId: string): Promise<TaskTerminals> {
  return request<TaskTerminals>(`/topics/${encodeURIComponent(taskId)}/terminals`)
}

/** End one of the caller's shells in the task. */
export function closeTaskTerminal(taskId: string, terminalId: string): Promise<{ closed: boolean }> {
  return request<{ closed: boolean }>(
    `/topics/${encodeURIComponent(taskId)}/terminals/${encodeURIComponent(terminalId)}`,
    { method: 'DELETE' }
  )
}
