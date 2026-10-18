// 文档之间的链接（#3827）：哪些文档、哪些对话引用了这一份。只列读的人能读的。
import { request } from './http'

/** 引用了这份文档的一处：另一份文档，或一个对话（频道、任务、支线）里的消息。 */
export type DocBacklink =
  | {
      kind: 'document'
      id: string
      project_id: string
      title: string | null
      /** 最近一次改动的人和时间。 */
      updated_by: string | null
      updated_at: string
    }
  | {
      kind: 'conversation'
      /** 引用出现在哪个对话：频道、任务或支线。 */
      conversation: { kind: 'room' | 'task' | 'thread'; id: string; title: string; room_id: string; project_id: string }
      /** 最近引用它的那条消息：跳过去用。 */
      message_id: string
      /** 那条消息的开头，原文（引用还是 `<#id>`，画的时候读成文字）。 */
      excerpt: string
      author: string
      at: string
      /** 这个对话里一共几条消息引用了它。 */
      count: number
    }

export function getDocumentBacklinks(documentId: string): Promise<{ linked_from: DocBacklink[] }> {
  return request<{ linked_from: DocBacklink[] }>(`/documents/${encodeURIComponent(documentId)}/links`)
}
