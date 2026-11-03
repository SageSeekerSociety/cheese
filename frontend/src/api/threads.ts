// 支线：主线上一条消息下面的回复，自成一段对话。它的 id 和房间、任务的一样用：
// 消息、连接、已读都走 `/topics/{支线}/…`。这里只有支线自己才有的几件事。
import type { Thread, ThreadLists } from '../types/threads'

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

/** 频道里的支线，两栏：开着的（最近有回复的在前），和收起来的（最近收的在前）。
 *  收起来的是报错满三天、没人回来的那条（`thread/recycle.py`）；它不来默认这一栏，
 *  但读得到，所以两栏一起回来。 */
export function listThreads(roomId: string, limit = 50): Promise<ThreadLists> {
  return request<ThreadLists>(`/topics/${encodeURIComponent(roomId)}/threads?limit=${limit}`)
}

/** 把一条收起来的支线放回默认那一栏。只有开这条支线的人和频道管理员点得动。 */
export function restoreThread(threadId: string): Promise<Thread> {
  return request<Thread>(`/topics/${encodeURIComponent(threadId)}/thread/restore`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

/** 在频道下直接开一条支线，不必先有话说（清单第 4 条）。开的人由会话认，不由请求体
 *  说；私聊里没有支线，那条路后端会拒。 */
export function newThread(roomId: string, title: string): Promise<Thread> {
  return request<Thread>(`/topics/${encodeURIComponent(roomId)}/threads`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}
