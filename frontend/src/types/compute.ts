// A session's execution environment (#2320). Every agent session has its own; a
// room, a task or a project only keeps the default a new session starts with
// (`GET /topics/{id}/environment`, `GET /projects/{id}/compute-configs`).

/**
 * A sandbox the platform places on some machine that offers `spec`; the machine
 * is never named to users. Specs are peers, like resource classes — `standard`,
 * `vm` (a virtual machine of its own, for Docker, KVM or kernel work), and
 * `team_devices` (the computers enrolled into the project's team) —
 * and the GET's `specs` says which there are and what each is called.
 */
export interface SandboxEnvironment {
  kind: 'sandbox'
  spec: string
}

/** Run directly on a team device, without isolation; only its team admins may choose it. */
export interface SelfHostedEnvironment {
  kind: 'self_hosted'
  device_id: string
}

export type Environment = SandboxEnvironment | SelfHostedEnvironment

/** One sandbox spec on offer, with its name in the reader's language. */
export interface SandboxSpec {
  spec: string
  label: string
  /** What it is and what it costs, in the reader's language via `i18n.description`
   *  (`offered_specs`, so both the project's settings and a room's menu have it). */
  description?: string
  i18n?: Record<string, unknown>
}

/** A machine enrolled into the project's team; the platform's own are never listed. */
export interface EnvironmentDevice {
  device_id: string
  name: string
  online: boolean
  // The reader may choose whole-machine execution as an administrator of its team.
  owned: boolean
}

/** One agent session in the conversation and the environment it works in. */
export interface SessionEnvironment {
  session_id: string
  agent_handle: string
  // The roster handle the session sits under (a member's own agent may differ
  // from `agent_handle`).
  seat_handle: string
  // null: it has not needed a machine yet, and will take the default.
  environment: Environment | null
  status: string | null
  online: boolean
}

// GET /topics/{id}/environment — a room's, or a task's (by the task's id).
export interface TopicEnvironment {
  default: Environment | null
  default_from: 'task' | 'room' | 'project' | 'deployment'
  sessions: SessionEnvironment[]
  devices: EnvironmentDevice[]
  // The sandbox specs this project can ask for.
  specs: SandboxSpec[]
}

// GET /projects/{id}/compute-configs — the environment new agents start in, and
// where the project's agents that have started are working now.
export interface ProjectComputeConfigs {
  default: Environment
  // How many of this project's turns may run at once, and whether that is the
  // project's own number or the deployment's.
  max_concurrent_turns: number
  max_concurrent_turns_from: 'project' | 'deployment'
  /** Whether this caller may move agents already on a device elsewhere (a manager's); the default is anyone's. */
  can_manage: boolean
  devices: EnvironmentDevice[]
  specs: SandboxSpec[]
  distribution: ComputeDistribution
}

export interface ComputeDistribution {
  // Sessions in sandboxes, by spec: where they run is the platform's, and is not shown.
  sandboxes: { spec: string; label: string; agents: number }[]
  // Sessions running directly on someone's machine.
  devices: { device_id: string; name: string | null; agents: number }[]
}
