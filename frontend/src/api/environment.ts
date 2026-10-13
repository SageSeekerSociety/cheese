// 项目和房间的工作环境：配置、套用，和「让芝士看看」。
import type { EnvironmentConfig, EnvironmentStatus, ProjectEnvironmentInfo } from '../cx_types'
import type { EnvironmentDiagnosis } from '../types/environment'

import { request } from './http'

/** 芝士读这个频道最近一次失败的日志和当时的脚本，说原因和改法；什么都不改。 */
export function diagnoseRoomEnvironment(projectId: string, roomId: string): Promise<EnvironmentDiagnosis> {
  return request<EnvironmentDiagnosis>(
    `/projects/${encodeURIComponent(projectId)}/environment/rooms/${encodeURIComponent(roomId)}/diagnose`,
    { method: 'POST' }
  )
}

export function getProjectEnvironment(projectId: string): Promise<ProjectEnvironmentInfo> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment`)
}
export function saveProjectEnvironment(
  projectId: string,
  config: Omit<EnvironmentConfig, 'revision'>
): Promise<EnvironmentConfig> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment`, {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}
export function getRoomEnvironment(projectId: string, roomId: string): Promise<EnvironmentStatus> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment/rooms/${encodeURIComponent(roomId)}`)
}
export function applyRoomEnvironment(projectId: string, roomId: string, latest: boolean): Promise<EnvironmentStatus> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment/rooms/${encodeURIComponent(roomId)}/apply`, {
    method: 'POST',
    body: JSON.stringify({ latest }),
  })
}
