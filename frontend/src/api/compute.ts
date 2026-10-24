import type { ProjectCredits } from '../cx_types'
import type { Environment, ProjectComputeConfigs, TopicEnvironment } from '../types/compute'
import type { CreditBudgetUpdate, ProjectCreditBudget } from '../types/creditBudgets'

import { request } from './http'

// 算力额度: the project's grant balance (unlimited when no grants).
export function getProjectCredits(projectId: string): Promise<ProjectCredits> {
  return request<ProjectCredits>(`/projects/${encodeURIComponent(projectId)}/credits`)
}

export function getProjectCreditBudget(projectId: string): Promise<ProjectCreditBudget> {
  return request(`/projects/${encodeURIComponent(projectId)}/credit-budget`)
}

export function saveProjectCreditBudget(projectId: string, update: CreditBudgetUpdate): Promise<ProjectCreditBudget> {
  return request(`/projects/${encodeURIComponent(projectId)}/credit-budget`, {
    method: 'PUT',
    body: JSON.stringify(update),
  })
}

// Creation defaults, available before a project exists.
export interface ResourceLimits {
  max_concurrent_turns: number
}

export function getResourceLimits(): Promise<ResourceLimits> {
  return request('/projects/resource-limits')
}

// 一段对话（房间，或者用任务的 id 时那个任务）的执行环境：新会话默认用哪个，和每条
// 会话各自在哪个环境里。
export function getTopicEnvironment(topicId: string): Promise<TopicEnvironment> {
  return request<TopicEnvironment>(`/topics/${encodeURIComponent(topicId)}/environment`)
}

// The project's agent sessions on one self-hosted device, for a project manager
// to switch some elsewhere. Rooms the caller cannot open are only counted.
export function listDeviceSessions(
  projectId: string,
  deviceId: string
): Promise<{ sessions: import('../types/deviceSessions').DeviceSession[]; hidden: number }> {
  return request(`/projects/${encodeURIComponent(projectId)}/devices/${encodeURIComponent(deviceId)}/sessions`)
}

export function getProjectComputeConfigs(projectId: string): Promise<ProjectComputeConfigs> {
  return request(`/projects/${encodeURIComponent(projectId)}/compute-configs`)
}

export function saveProjectComputeConfigs(
  projectId: string,
  configs: Pick<ProjectComputeConfigs, 'default'>
): Promise<Pick<ProjectComputeConfigs, 'default'>> {
  return request(`/projects/${encodeURIComponent(projectId)}/compute-configs`, {
    method: 'PUT',
    body: JSON.stringify(configs),
  })
}

// 撞上项目档位策略时这次选择没有发生，换来的是一条给人的提议 —— 接口照样 200，
// 所以「有没有 proposal」是调用方唯一能看出区别的地方（backend
// `domain/policy/gate.py`）。丢掉它就等于告诉点了按钮的人什么也没发生。
export interface ComputeProposal {
  approver: string
  tier: string
  content: string
}

// 新会话默认：只管以后开工的会话，已经在干活的一条也不搬。null 让它跟上一级走。
export function setEnvironmentDefault(
  topicId: string,
  environment: Environment | null
): Promise<{ proposal?: ComputeProposal | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/environment-default`, {
    method: 'PUT',
    body: JSON.stringify({ environment }),
  })
}

// 换一条会话的环境：只搬这一条。搬之前它尽力推送一次，推没推上去都换。`ifIdle` 跳过
// 正在干活的（409 SessionWorking）。
export function setSessionEnvironment(
  topicId: string,
  sessionId: string,
  environment: Environment,
  options: { ifIdle?: boolean } = {}
): Promise<{ proposal?: ComputeProposal | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/sessions/${encodeURIComponent(sessionId)}/environment`, {
    method: 'PUT',
    body: JSON.stringify({ environment, ...(options.ifIdle ? { if_idle: true } : {}) }),
  })
}
