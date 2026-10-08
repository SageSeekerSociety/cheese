import type { MarketNodes, MarketPools, ProjectCredits } from '../cx_types'
import type { ComputeChoice, ProjectComputeConfigs, TopicComputeProfile } from '../types/compute'

import { request } from './http'

// ---- 资源池市场 + 项目设置 (design v3) ----

// The full 市场 catalog: every AI pool + compute pool on offer.
export function getMarketPools(): Promise<MarketPools> {
  return request<MarketPools>('/market/pools')
}

// 节点看板: configured compute nodes with liveness + current load.
export function getMarketNodes(): Promise<MarketNodes> {
  return request<MarketNodes>('/market/nodes')
}

// 算力额度: the project's grant balance (unlimited when no grants).
export function getProjectCredits(projectId: string): Promise<ProjectCredits> {
  return request<ProjectCredits>(`/projects/${encodeURIComponent(projectId)}/credits`)
}

// ---- 题目匹配市场 (spec §13 阶段 6) ----

// Creation defaults, available before a project exists.
export interface ResourceLimits {
  max_concurrent_turns: number
}

export function getResourceLimits(): Promise<ResourceLimits> {
  return request('/projects/resource-limits')
}

// 房间的工作电脑：房间这一项（还没开工的 AI 队友开工时用哪台），和每个会话在哪台上。
// A task's id: that task's own work computer.
export function getTopicComputeProfile(topicId: string): Promise<TopicComputeProfile> {
  return request<TopicComputeProfile>(`/topics/${encodeURIComponent(topicId)}/compute-profile`)
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

// 一个话题一个容器：改的是整个房间，每条会话都跟着搬，搬之前尽力推送一次，推没推上去都搬。`ifIdle` 跳过正在干活的
// 房间（409 SessionWorking）；`visibility` 是房间在点名那台上能看到什么，不给就保持原样，新绑上的是隔离环境。
export function setTopicComputeChoice(
  topicId: string,
  choice: ComputeChoice,
  options: {
    ifIdle?: boolean
    visibility?: 'host' | 'isolated'
  } = {}
): Promise<{ choice: ComputeChoice; proposal: ComputeProposal | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/compute-profile`, {
    method: 'PUT',
    body: JSON.stringify({
      choice,
      ...(options.ifIdle ? { if_idle: true } : {}),
      ...(options.visibility ? { visibility: options.visibility } : {}),
    }),
  })
}
