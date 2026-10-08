// 项目自己的文档：资料库里那些能一起改的文档。对话自带的那一份不在列表里，
// 但资料库的搜索找得到它，可以另存一份进来。
import type { DocumentSearch, ProjectDocument } from '../lib/projectDocument'

import { rememberNumbered } from '../lib/addresses'

import { request } from './http'

export type { DocumentHit, DocumentSearch, ProjectDocument } from '../lib/projectDocument'

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/documents`
const one = (documentId: string) => `/documents/${encodeURIComponent(documentId)}`

/** 列表里的一份文档，带着它在资料库页那张混排的表里的位置（后端 `app/core/rank.py`）。 */
export type ListedDocument = ProjectDocument & { rank: string }

/** 项目自己的文档，一页一页地给，最近改过的在前；`next` 是下一页的游标。 */
export function listProjectDocuments(
  projectId: string,
  cursor: string | null = null,
  limit = 50
): Promise<{ data: ListedDocument[]; next: string | null }> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (cursor) params.set('cursor', cursor)
  return request<{ data: ListedDocument[]; next: string | null }>(`${base(projectId)}?${params}`).then((page) => {
    rememberNumbered('docs', page.data)
    return page
  })
}

/** 新建一份：空的，或者另存 `copyOf` 那一份现在的样子。 */
export function createProjectDocument(
  projectId: string,
  body: { title?: string; copy_of?: string } = {}
): Promise<ProjectDocument> {
  return request<ProjectDocument>(base(projectId), { method: 'POST', body: JSON.stringify(body) })
}

export function searchProjectDocuments(projectId: string, q: string): Promise<DocumentSearch> {
  return request<DocumentSearch>(`${base(projectId)}/search?q=${encodeURIComponent(q)}`)
}

/** 这份文档叫什么、是谁的；还没写过字的也答得出。 */
export function getDocumentAbout(documentId: string): Promise<ProjectDocument> {
  return request<ProjectDocument>(`${one(documentId)}/about`)
}

export function renameDocument(documentId: string, title: string): Promise<ProjectDocument> {
  return request<ProjectDocument>(one(documentId), { method: 'PATCH', body: JSON.stringify({ title }) })
}

export function deleteDocument(documentId: string): Promise<unknown> {
  return request(one(documentId), { method: 'DELETE' })
}

/** 这份文档最后存下的正文；还没写过字时是空的。 */
export async function getDocumentText(documentId: string): Promise<string> {
  const stored = await request<{ content: string } | null>(one(documentId))
  return stored?.content ?? ''
}

/** 项目总览是哪一份文档（第一次问时建出空的一份）。综合的概览和项目文档页都显示它。 */
export function getProjectOverview(projectId: string): Promise<{ id: string }> {
  return request<{ id: string }>(`/projects/${encodeURIComponent(projectId)}/overview`)
}
