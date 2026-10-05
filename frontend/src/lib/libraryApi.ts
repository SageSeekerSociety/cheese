// 资料库的接口：一份资料是什么样、怎么放进来、怎么换新、怎么读它的字节。
//
// 列清单、下载、删除还在 api.ts 里（别处早就在用）；这里是资料库页自己的那几样：上传、
// 替换、读字节、版本列表、按版本下载、恢复旧版。
import type { ApiEnvelope } from '@/cx_types'

import { authToken, BASE, libraryFileRawUrl, request } from '../api'
import { t } from '../i18n'

import { refusalWords } from './noticeText'

function auth(): Record<string, string> {
  const token = authToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export interface LibraryFile {
  path: string
  bytes: number
  /** Unix seconds; the list comes back newest first. */
  modified: number
  /** 谁放进来的（handle）；查不到时为 null。 */
  added_by: string | null
  added_at: string | null
  /** 在哪个房间给的；从资料库页直接上传的、或读不了那个房间时为 null。 */
  room: { id: string; title: string; title_source?: string } | null
  /** 被替换过几次。 */
  replaced: number
  /** 你读得到的房间里有几条消息带着它。 */
  references: number
}

async function libraryUpload(
  projectId: string,
  file: File,
  replacing?: string
): Promise<{ path: string; bytes: number }> {
  const form = new FormData()
  form.append('file', file)
  const query = replacing ? `?path=${encodeURIComponent(replacing)}` : ''
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(projectId)}/library${query}`, {
    method: replacing ? 'PUT' : 'POST',
    body: form,
    headers: auth(),
  })
  const envelope = (await res.json().catch(() => null)) as ApiEnvelope<{ path: string; bytes: number }> | null
  if (!res.ok || !envelope || envelope.code !== 200) {
    throw new Error(refusalWords(envelope) || t('files.library.uploadFailed', { status: res.status }))
  }
  return envelope.data
}

/** 在资料库页上直接放进一份文件。撞名不覆盖，返回它最后叫什么。 */
export function uploadLibraryFile(projectId: string, file: File): Promise<{ path: string; bytes: number }> {
  return libraryUpload(projectId, file)
}

/** 把一份资料换成新版本：名字不变，旧的那一份留着。 */
export function replaceLibraryFile(
  projectId: string,
  path: string,
  file: File
): Promise<{ path: string; bytes: number }> {
  return libraryUpload(projectId, file, path)
}

/** 一份资料的字节，给页面预览；Office 文档可以转成 PDF 再给。 */
export async function libraryFileBytes(projectId: string, path: string, asPdf = false): Promise<ArrayBuffer> {
  const response = await fetch(libraryFileRawUrl(projectId, path) + (asPdf ? '&preview_pdf=true' : ''), {
    headers: auth(),
  })
  if (response.ok) return response.arrayBuffer()
  let message = ''
  try {
    message = String((await response.json())?.message || '')
  } catch {
    /* Keep the HTTP error when the server sent no JSON. */
  }
  throw new Error(message || t('files.library.readFailed', { status: response.status }))
}

/** 一份资料的一版（版本列表里的一行）。 */
export interface LibraryVersion {
  /** 记录 id；记录表之前就在、从没被替换过的那一份没有 id。 */
  id: string | null
  /** 按放进来的先后从 1 数。 */
  version: number
  bytes: number
  /** 谁放进来的（这一版是谁换上的）。 */
  added_by: string | null
  added_at: string | null
  current: boolean
}

/** 一份资料的每一版，新的在前。 */
export async function listLibraryVersions(projectId: string, path: string): Promise<LibraryVersion[]> {
  const out = await request<{ versions: (Omit<LibraryVersion, 'added_at'> & { created_at: string | null })[] }>(
    `/projects/${encodeURIComponent(projectId)}/library/versions?path=${encodeURIComponent(path)}`
  )
  return out.versions.map(({ created_at, ...rest }) => ({ ...rest, added_at: created_at }))
}

/** 某一版的字节地址（给 `downloadFile`）。 */
export function libraryVersionRawUrl(projectId: string, path: string, versionId: string): string {
  return `${libraryFileRawUrl(projectId, path)}&version=${encodeURIComponent(versionId)}`
}

/** 把旧的一版恢复成现在这一份：复制成新的一版，历史只增不减。 */
export function restoreLibraryVersion(projectId: string, path: string, versionId: string): Promise<unknown> {
  const query = `path=${encodeURIComponent(path)}&version=${encodeURIComponent(versionId)}`
  return request(`/projects/${encodeURIComponent(projectId)}/library/restore?${query}`, { method: 'POST' })
}
