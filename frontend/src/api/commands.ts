// The commands of a task: what its AI teammates ran in a shell, each with what it printed
// (`GET /topics/{task}/commands`, read off the task's record), and the commands a project keeps
// under a name (`/projects/{project}/commands`). A person runs either again in their own terminal.
import { request } from './http'

/** One shell command a teammate ran in the task. */
export interface RanCommand {
  call_id: string
  command: string
  /** The teammate's one line about it, when it gave one. */
  description: string
  /** Started to run on its own: what it printed later is not here. */
  background: boolean
  /** What came back; null while it runs. */
  output: string | null
  failed: boolean
  at: string
  turn_id: string | null
  agent_handle: string
}

export interface RanCommandsPage {
  /** Newest first. */
  items: RanCommand[]
  /** Where the next, older page starts, while there is one. */
  next: string | null
}

/** A command kept under a name in a project. */
export interface NamedCommand {
  id: string
  name: string
  command: string
  fixed_by: string
  fixed_at: string
}

export function listRanCommands(taskId: string, before?: string | null): Promise<RanCommandsPage> {
  const query = before ? `?before=${encodeURIComponent(before)}` : ''
  return request<RanCommandsPage>(`/topics/${encodeURIComponent(taskId)}/commands${query}`)
}

function namedPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/commands`
}

export async function listNamedCommands(projectId: string): Promise<NamedCommand[]> {
  return (await request<{ items: NamedCommand[] }>(namedPath(projectId))).items
}

export function nameCommand(projectId: string, name: string, command: string): Promise<NamedCommand> {
  return request<NamedCommand>(namedPath(projectId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, command }),
  })
}

export function removeNamedCommand(projectId: string, id: string): Promise<unknown> {
  return request(`${namedPath(projectId)}/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
