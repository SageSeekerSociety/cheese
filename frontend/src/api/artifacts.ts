import type { ListPayload } from '../cx_types'

import { t } from '../i18n'

import { authHeaders, BASE, request } from './http'

// ---- 产物清单 ----

// 这个项目交出去的东西，一项一行。清单由交付长出来，所以这里没有「新建」——
// 能做的三件事都是人的判断：改名、合并、删除。
export interface ProjectArtifact {
  id: string
  name: string
  /** 一句话：这是什么东西、给谁的。交付时写下，没人写过时是空串。 */
  about: string
  /** 交付过几次。0 = 有一张卡正在交付它，但还没有哪一次落地。 */
  version: number
  /** 最近一次交付被采纳的时刻（ISO），一次都还没有时为 null。 */
  delivered_at: string | null
}

export function listProjectArtifacts(projectId: string): Promise<ListPayload<ProjectArtifact>> {
  return request<ListPayload<ProjectArtifact>>(`/projects/${encodeURIComponent(projectId)}/artifacts`)
}

/** 交出去的是什么形态：一份文件、一个地址、一次合并。null = 这一版是交付物落地
 *  之前递的卡，当时没有记，而那份构建产物已经不在了。 */
export type DeliverableKind = 'file' | 'link' | 'merge'

/** 这一项的第 N 版 —— 就是第 N 张采纳了的卡。 */
export interface ArtifactVersion {
  number: number
  card_id: string
  /** 这次交付改了什么（卡上那句提交标题）。 */
  subject: string | null
  delivered_at: string | null
  decided_by: string | null
  kind: DeliverableKind | null
  filename: string | null
  url: string | null
  bytes: number | null
  room: { id: string; title: string } | null
}

export interface ProjectArtifactDetail extends ProjectArtifact {
  versions: ArtifactVersion[]
}

export interface ArtifactComparison {
  kind: 'file' | 'merge' | 'link' | 'unavailable'
  identical: boolean | null
  note: string | null
  files: {
    path: string
    diff: string | null
    note: string | null
    status?: string
  }[]
}

export function compareArtifactVersions(
  projectId: string,
  artifactId: string,
  before: string,
  after: string
): Promise<ArtifactComparison> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/compare?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`
  )
}

export async function artifactVersionBytes(
  projectId: string,
  artifactId: string,
  cardId: string,
  asPdf = false
): Promise<ArrayBuffer> {
  const response = await fetch(
    artifactVersionFileUrl(projectId, artifactId, cardId) + (asPdf ? '?preview_pdf=true' : ''),
    { headers: authHeaders() }
  )
  if (response.ok) return response.arrayBuffer()
  let message = ''
  try {
    message = String((await response.json())?.message || '')
  } catch {
    /* Keep the HTTP error when the server sent no JSON. */
  }
  throw new Error(message || t('global.request.versionReadFailed', { status: response.status }))
}

export function getProjectArtifact(projectId: string, artifactId: string): Promise<ProjectArtifactDetail> {
  return request<ProjectArtifactDetail>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`
  )
}

/** 这一版当时交出去的那一份字节。取的是快照，不是现在重建一次的结果。 */
export function artifactVersionFileUrl(projectId: string, artifactId: string, cardId: string): string {
  return (
    `${BASE}/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}` +
    `/versions/${encodeURIComponent(cardId)}/file`
  )
}

/** 换个名字。卡指着的是这一项的 id，所以之前的交付照样算它的版本。 */
export function renameProjectArtifact(
  projectId: string,
  artifactId: string,
  name: string
): Promise<{ id: string; name: string }> {
  return request<{ id: string; name: string }>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`,
    { method: 'PATCH', body: JSON.stringify({ name }) }
  )
}

/** 这两项其实是同一个东西：把 `artifactId` 的交付都算到 `into` 上，它自己没了。 */
export function mergeProjectArtifacts(
  projectId: string,
  artifactId: string,
  into: string
): Promise<{ id: string; name: string }> {
  return request<{ id: string; name: string }>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/merge`,
    { method: 'POST', body: JSON.stringify({ into }) }
  )
}

export function deleteProjectArtifact(projectId: string, artifactId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`,
    { method: 'DELETE' }
  )
}
