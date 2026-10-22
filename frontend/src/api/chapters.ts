// 一段对话的章节：把某条消息钉成这一节的开始（消息的 ⋯ 里），在概览里列出来、点一条
// 跳回去。章节跟对话走：频道是频道它自己，任务是任务的 id。
import type { ChapterRow } from '../types/chapters'

import { request } from './http'

function chaptersPath(conversationId: string): string {
  return `/topics/${encodeURIComponent(conversationId)}/chapters`
}

export function listChapters(conversationId: string): Promise<ChapterRow[]> {
  return request<ChapterRow[]>(chaptersPath(conversationId))
}

export function addChapter(conversationId: string, blockId: string): Promise<unknown> {
  return request(`${chaptersPath(conversationId)}/${encodeURIComponent(blockId)}`, { method: 'PUT' })
}

export function removeChapter(conversationId: string, blockId: string): Promise<unknown> {
  return request(`${chaptersPath(conversationId)}/${encodeURIComponent(blockId)}`, { method: 'DELETE' })
}

export function renameChapter(conversationId: string, blockId: string, title: string): Promise<unknown> {
  return request(`${chaptersPath(conversationId)}/${encodeURIComponent(blockId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}
