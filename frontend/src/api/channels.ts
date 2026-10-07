// 项目的频道列表，以及管项目的人不进频道也能做的两件事：换管理者、加入私密频道。
import type { ChannelDirectory } from '../types/channelDirectory'

import { request } from './http'

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/channels`

export function listChannels(projectId: string): Promise<ChannelDirectory> {
  return request<ChannelDirectory>(base(projectId))
}

/** 管项目的人加入一个自己不在里面的私密频道；频道里会留一行。 */
export function stepIntoChannel(projectId: string, channelId: string): Promise<unknown> {
  return request(`${base(projectId)}/${encodeURIComponent(channelId)}/step-in`, { method: 'POST' })
}

export function handOverChannel(projectId: string, channelId: string, handle: string): Promise<unknown> {
  return request(`${base(projectId)}/${encodeURIComponent(channelId)}/manager`, {
    method: 'PUT',
    body: JSON.stringify({ handle }),
  })
}
