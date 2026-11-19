// A person working on a task's files: bringing the task's machine back for
// their edits (backend `topics_task_machine`), and creating, renaming and
// deleting files and folders of the task's copy (`workspace.arrange_file`).
import { request } from './http'

export interface TaskMachinePrepare {
  started: boolean
  reason: 'preparing' | null
}

export interface TaskMachineStatus {
  /** `ready`: a machine holds the task's copy; `preparing`: one is being brought back. */
  machine: 'ready' | 'preparing' | null
  /** Why the last bring-up did not finish. */
  failed: 'no_session' | 'unavailable' | null
}

/** Start bringing the task's machine back for the caller; answers at once. */
export function prepareTaskMachine(taskId: string): Promise<TaskMachinePrepare> {
  return request<TaskMachinePrepare>(`/topics/${encodeURIComponent(taskId)}/machine/prepare`, { method: 'POST' })
}

/** How far bringing the task's machine back has got. */
export function getTaskMachineStatus(taskId: string): Promise<TaskMachineStatus> {
  return request<TaskMachineStatus>(`/topics/${encodeURIComponent(taskId)}/machine/status`)
}

export type FileArrangement =
  | { operation: 'create' | 'mkdir' | 'delete'; path: string }
  | { operation: 'move'; path: string; to: string }

/** Create a file or folder, rename or delete one, in the task's copy. Nothing is replaced. */
export function arrangeTaskFile(projectId: string, taskId: string, change: FileArrangement): Promise<{ path: string }> {
  return request<{ path: string }>(
    `/projects/${encodeURIComponent(projectId)}/file/arrange?${new URLSearchParams({ task: taskId })}`,
    { method: 'POST', body: JSON.stringify(change) }
  )
}
