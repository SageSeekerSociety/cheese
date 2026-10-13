import type { AgentConfiguration, AgentType, ListPayload, ProjectAgent } from '../cx_types'
import type { AgentFieldChoice } from '../lib/modelChoices'

import { request } from './http'

// ---- AI 队友 (agent 类型与实例) ----
//
// 「不能停用最后一个」and the like are the backend's to enforce; these are plain
// transports. What they must NOT do is paper over a missing endpoint: the agent
// backend lands separately, so a 404 here has to reach the caller as a 404 (see
// `isEndpointMissing`) rather than as an empty list that reads like "no agents".

// Project main and native subagent model defaults.
export interface ProjectDefaultModel {
  subagent_model: string | null
  /** 项目显式设的模型；null = 没设，走 deployment_default */
  model: string | null
  /** 没设显式默认时，部署兜底算出来的那个 */
  deployment_default: string | null
  /** 当前项目能用的全部模型，每个带 default 标记（项目显式设过的那条=True） */
  choices: AgentFieldChoice[]
  can_manage: boolean
}

export function getProjectDefaultModel(projectId: string): Promise<ProjectDefaultModel> {
  return request(`/projects/${encodeURIComponent(projectId)}/default-model`)
}

export function setProjectDefaultModel(
  projectId: string,
  model: string | null,
  subagentModel?: string | null
): Promise<ProjectDefaultModel> {
  return request(`/projects/${encodeURIComponent(projectId)}/default-model`, {
    method: 'PUT',
    body: JSON.stringify({ model, subagent_model: subagentModel }),
  })
}

// Built-in starting configurations, copied only when creating an agent.
export function listAgentTypes(): Promise<ListPayload<AgentType>> {
  return request<ListPayload<AgentType>>('/agent-types')
}

export function listProjectAgents(projectId: string): Promise<ListPayload<ProjectAgent>> {
  return request<ListPayload<ProjectAgent>>(`/projects/${encodeURIComponent(projectId)}/agents`)
}

export function createProjectAgent(
  projectId: string,
  payload: { display_name: string; handle?: string; type_name?: string | null; configuration: AgentConfiguration }
): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/agents`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateProjectAgent(
  projectId: string,
  agentId: string,
  payload: { display_name?: string; configuration?: AgentConfiguration }
): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

// 停用 — not a physical delete. Topics already using it keep working and its
// memory is kept; it just stops being选得到 for new ones.
export function deactivateProjectAgent(projectId: string, agentId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}`,
    { method: 'DELETE' }
  )
}

// Select the existing agent that new rooms start with.
export function setProjectDefaultAgent(projectId: string, body: { instance_id: string }): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/default-agent`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
