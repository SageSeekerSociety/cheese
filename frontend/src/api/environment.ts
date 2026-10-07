// 项目的工作环境里不在 api.ts 的那一部分：「让芝士看看」。
import type { EnvironmentDiagnosis } from '../types/environment'

import { request } from './http'

/** 芝士读这个频道最近一次失败的日志和当时的脚本，说原因和改法；什么都不改。 */
export function diagnoseRoomEnvironment(projectId: string, roomId: string): Promise<EnvironmentDiagnosis> {
  return request<EnvironmentDiagnosis>(
    `/projects/${encodeURIComponent(projectId)}/environment/rooms/${encodeURIComponent(roomId)}/diagnose`,
    { method: 'POST' }
  )
}
