// 团队和个人名下的文档（#3774）：只属于团队、不属于哪个项目的那些。个人名下的文档
// 属于自己的个人团队。读写都是团队成员；删除和移动是作者和团队管理员。
import type { ProjectDocument } from '../lib/projectDocument'
import type { ListedDocument } from './projectDocuments'

import { request } from './http'

const base = (teamId: number) => `/teams/${teamId}/documents`

/** 团队的文档，最近改过的在前。 */
export function listTeamDocuments(teamId: number): Promise<{ data: ListedDocument[]; next: string | null }> {
  return request<{ data: ListedDocument[]; next: string | null }>(base(teamId))
}

/** 在团队（或自己名下）新建一份空文档。 */
export function createTeamDocument(teamId: number, body: { title?: string } = {}): Promise<ProjectDocument> {
  return request<ProjectDocument>(base(teamId), { method: 'POST', body: JSON.stringify(body) })
}

/** 一份文档要挪到的地方：一个团队（自己名下也是一个团队），和它的一个项目或不属于项目。 */
export interface DocumentPlace {
  team_id: number
  project_id: string | null
}

/** 「移动到…」能列出来的一处：一个团队和它没归档的项目。 */
export interface MovablePlace {
  team_id: number
  /** 地址里的那一段：`/teams/<handle>`；自己名下是自己的用户名。 */
  handle: string
  name: string
  personal: boolean
  projects: { id: string; name: string }[]
}

/** 这份文档能挪到哪些地方：自己名下，和自己所在的每个团队连同它们的项目。 */
export function getDocumentPlaces(documentId: string): Promise<{ places: MovablePlace[] }> {
  return request<{ places: MovablePlace[] }>(`/documents/${encodeURIComponent(documentId)}/places`)
}

/** 把文档挪到另一层。挪了之后谁能读跟着变，页面先问过人。 */
export function moveDocument(documentId: string, to: DocumentPlace): Promise<ProjectDocument> {
  return request<ProjectDocument>(`/documents/${encodeURIComponent(documentId)}/place`, {
    method: 'PUT',
    body: JSON.stringify(to),
  })
}
