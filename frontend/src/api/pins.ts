// 频道的置顶：成员把主线上的一条消息或一个文件钉在概览最上面。置顶在主线上原地做
// （消息和文件的 ⋯ 里），在概览里取消。
import type { ChannelPin } from '../types/channels'

import { request } from './http'

export type { ChannelPin }

function pinsPath(topicId: string): string {
  return `/topics/${encodeURIComponent(topicId)}/pins`
}

export function listPins(topicId: string): Promise<ChannelPin[]> {
  return request<ChannelPin[]>(pinsPath(topicId))
}

export function pinBlock(topicId: string, blockId: string): Promise<unknown> {
  return request(`${pinsPath(topicId)}/${encodeURIComponent(blockId)}`, { method: 'PUT' })
}

export function unpinBlock(topicId: string, blockId: string): Promise<unknown> {
  return request(`${pinsPath(topicId)}/${encodeURIComponent(blockId)}`, { method: 'DELETE' })
}
