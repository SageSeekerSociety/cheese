// The room's view of its live sessions: what `GET /topics/{id}/agent/control`
// answers and the `agent_control` socket frame carries.
export interface AgentControlState {
  id: string | null
  connected: boolean
  /** The seat whose session this is; null when none was named and the room has
   * no single live one. */
  agent?: string | null
  /** Every live seat in the room with its session: two or more and none named
   * leaves `id` null, and the caller picks one. */
  seats?: { agent: string; id: string }[]
  controls?: string[]
  tasks?: Record<
    string,
    {
      task_id: string
      description?: string
      status?: string
      subtype?: string
      tool_use_id?: string
      task_type?: string
    }
  >
  state?: Record<string, Record<string, unknown>>
}
