// 支线：主线上一条消息下面的回复，自成一段对话。它的 id 和房间、任务的一样用：
// 消息、连接、已读都走 `/topics/{支线}/…`。这里只有支线自己才有的三件事。
import type { Thread, ThreadRow } from '../types/threads'

import { request } from './http'

/** 这条主线消息下面的支线；还没有就开一条。同一条消息只有一条支线。 */
export function openThread(blockId: string): Promise<Thread> {
  return request<Thread>(`/blocks/${encodeURIComponent(blockId)}/thread`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

/** 一条支线，带着它挂着的那条消息。 */
export function getThread(threadId: string): Promise<Thread> {
  return request<Thread>(`/topics/${encodeURIComponent(threadId)}/thread`)
}

/** 频道里有回复的支线，最近有回复的在前。 */
export function listThreads(roomId: string, limit = 50): Promise<ThreadRow[]> {
  return request<ThreadRow[]>(`/topics/${encodeURIComponent(roomId)}/threads?limit=${limit}`)
}
