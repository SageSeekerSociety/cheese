import type { Block, ListPayload, TopicProgress } from '../cx_types'
import type { DocComment } from '../lib/docThreadTypes'

import { request } from './http'

// 进度层 (#187): 芝士's checklist as of the last turn that touched this topic.
// Read on topic open — between turns there is no WS stream to carry it, and
// "做到哪了" has to be visible without summoning anyone. `items` is [] for a
// topic that never had a checklist. A task's id reads the list its own session
// wrote.
export function getProgress(topicId: string): Promise<TopicProgress> {
  return request<TopicProgress>(`/topics/${encodeURIComponent(topicId)}/progress`)
}

// A document's top-level blocks (heading/paragraph/list/…), in order: which
// passage each comment is aligned to.
export function getDocNodes(documentId: string): Promise<{ data: Block[]; total: number }> {
  return request<{ data: Block[]; total: number }>(`/documents/${encodeURIComponent(documentId)}/nodes`)
}

/** Start a comment thread on the words `quote` (or on the whole document without them). */
export function addComment(documentId: string, content: string, quote?: string): Promise<DocComment> {
  return request<DocComment>(`/documents/${encodeURIComponent(documentId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ content, quote: quote || undefined }),
  })
}

// 周报集 (spec §7.1): the project's weekly reports, newest first. Each Block
// carries the stretch it covers in `meta` (`since`/`until`) and points back to
// the room it was written in via `topic_id`.
export function getProjectWeeklies(projectId: string): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(`/projects/${encodeURIComponent(projectId)}/weeklies`)
}

// 叫芝士现在就读它还没读到的消息（一轮失败之后的「重试」）。不发新消息 —— 那些
// 消息已经在时间线上了，补一条一模一样的只会让人分不清哪条是真的。
// `started` 为 false 时说明这一下没必要（房间已经在干活，或者没有待读的东西）。
export function summonAgent(topicId: string): Promise<{ started: boolean; reason?: string }> {
  return request<{ started: boolean; reason?: string }>(`/topics/${encodeURIComponent(topicId)}/summon`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}
