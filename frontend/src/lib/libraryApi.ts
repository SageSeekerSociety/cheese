// 资料库的接口：一份资料是什么样、怎么放进来、怎么换新、怎么读它的字节。
//
// 下载地址和删除还在 api.ts 里（别处早就在用）；这里是列清单（一页一页）、上传、替换、
// 读字节、版本列表、按版本下载、恢复旧版。
import type { ApiEnvelope } from '@/cx_types'

import { authToken, BASE, libraryFileRawUrl, request } from '../api'
import { t } from '../i18n'

import { libraryParams, type LibraryQuery } from './libraryQuery'
import { refusalWords } from './noticeText'

function auth(): Record<string, string> {
  const token = authToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export interface LibraryFile {
  type: 'file'
  /** 这一行在资料库页那张混排的表里的位置，也是翻页的游标（后端 `app/core/rank.py`）。 */
  rank: string
  path: string
  bytes: number
  /** Unix seconds; the list comes back newest first. */
  modified: number
  /** 谁放进来的（handle）；查不到时为 null。 */
  added_by: string | null
  added_at: string
  /** 在哪个房间给的；从资料库页直接上传的、或读不了那个房间时为 null。 */
  room: { id: string; title: string } | null
  /** 被替换过几次。 */
  replaced: number
  /** 你读得到的房间里有几条消息带着它。 */
  references: number
}

/** 一层里的一个文件夹：名字里 `/` 前面那一段，后端从记录表里聚出来。 */
export interface LibraryFolder {
  type: 'folder'
  rank: string
  /** 整条路径，`合同/2026`。 */
  path: string
  /** 最后一层，`2026`。 */
  name: string
  /** 里面（含更深几层）有几份文件。 */
  count: number
  /** 里面最近放进来的那一份的时间（Unix 秒）。 */
  modified: number
}

export type LibraryEntry = LibraryFile | LibraryFolder

export type { LibraryQuery } from './libraryQuery'

export interface LibraryPage {
  data: LibraryEntry[]
  next: string | null
}

/** 资料库的一页（条件见 `LibraryQuery`）；`next` 是下一页的游标。 */
export function listProjectLibrary(projectId: string, query: LibraryQuery = {}): Promise<LibraryPage> {
  return request<LibraryPage>(`/projects/${encodeURIComponent(projectId)}/library?${libraryParams(query)}`)
}

/** 地址上点名的那一份：它不一定在已经取回来的那几页里。 */
export function getLibraryFile(projectId: string, path: string): Promise<LibraryFile> {
  return request<LibraryFile>(
    `/projects/${encodeURIComponent(projectId)}/library/file?path=${encodeURIComponent(path)}`
  )
}

/** 资料库里的每一个文件夹（整条路径），给「移动到」挑。 */
export async function listLibraryFolders(projectId: string): Promise<string[]> {
  return (await request<{ folders: string[] }>(`/projects/${encodeURIComponent(projectId)}/library/folders`)).folders
}

async function libraryUpload(
  projectId: string,
  file: File,
  replacing?: string,
  folder?: string
): Promise<{ path: string; bytes: number }> {
  const form = new FormData()
  form.append('file', file)
  if (folder) form.append('folder', folder)
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

/** 在资料库页上直接放进一份文件，`folder` 给了就放进那个文件夹。撞名不覆盖，返回它最后叫什么。 */
export function uploadLibraryFile(
  projectId: string,
  file: File,
  folder?: string
): Promise<{ path: string; bytes: number }> {
  return libraryUpload(projectId, file, undefined, folder)
}

/** 把一份资料或一个文件夹改名、挪到别处：`to` 是它的新名字。引用旧名字的消息跟着改。 */
export function moveLibraryFile(projectId: string, path: string, to: string): Promise<unknown> {
  return request(`/projects/${encodeURIComponent(projectId)}/library/move`, {
    method: 'POST',
    body: JSON.stringify({ path, to }),
  })
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
  /** 记录 id。 */
  id: string
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
