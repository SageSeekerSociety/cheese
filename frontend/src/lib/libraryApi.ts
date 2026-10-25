// 资料库的接口：一份资料是什么样、怎么放进来、怎么换新、怎么读它的字节。
//
// 下载地址和删除还在 api.ts 里（别处早就在用）；这里是列清单（一页一页）、上传、替换、
// 读字节、版本列表、按版本下载、恢复旧版。
import {
  authToken,
  libraryBase,
  libraryFileRawUrl,
  type LibraryShelf,
  request,
  sendInParts,
  type UploadProgress,
} from '../api'
import { t } from '../i18n'

import { libraryParams, type LibraryQuery } from './libraryQuery'

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
  /** 从题目来的那一份（项目资料库的 `题目/` 底下）：只能读，不能替换、改名、删除。 */
  readonly?: boolean
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
  /** `题目` 那个文件夹和它底下的：只能读，什么也放不进去。 */
  readonly?: boolean
}

export type LibraryEntry = LibraryFile | LibraryFolder

export type { LibraryQuery } from './libraryQuery'
export type { LibraryShelf }

export interface LibraryPage {
  data: LibraryEntry[]
  next: string | null
}

/** 资料库的一页（条件见 `LibraryQuery`）；`next` 是下一页的游标。项目的和团队的都是
 *  这一条（`LibraryShelf`）。 */
export function listProjectLibrary(shelf: LibraryShelf, query: LibraryQuery = {}): Promise<LibraryPage> {
  return request<LibraryPage>(`${libraryBase(shelf)}?${libraryParams(query)}`)
}

/** 挑一份资料用的那一页：只要文件，不要文件夹——`@` 到的是一份东西，不是一个目录。
 *  一次取几份由调用方定（`limit`）：菜单摆得下的比一屏少，取多了也是白取。 */
export async function searchLibraryFiles(shelf: LibraryShelf, q: string, limit: number): Promise<LibraryFile[]> {
  const page = await listProjectLibrary(shelf, { flat: true, q, limit })
  return page.data.filter((row): row is LibraryFile => row.type === 'file')
}

/** 地址上点名的那一份：它不一定在已经取回来的那几页里。 */
export function getLibraryFile(shelf: LibraryShelf, path: string): Promise<LibraryFile> {
  return request<LibraryFile>(`${libraryBase(shelf)}/file?path=${encodeURIComponent(path)}`)
}

/** 资料库里的每一个文件夹（整条路径），给「移动到」挑。 */
export async function listLibraryFolders(shelf: LibraryShelf): Promise<string[]> {
  return (await request<{ folders: string[] }>(`${libraryBase(shelf)}/folders`)).folders
}

async function libraryUpload(
  shelf: LibraryShelf,
  file: File,
  replacing?: string,
  folder?: string,
  progress?: UploadProgress
): Promise<{ path: string; bytes: number }> {
  const base = libraryBase(shelf)
  const uploaded = await sendInParts(base, file, progress)
  if (replacing) {
    return request(`${base}?path=${encodeURIComponent(replacing)}`, {
      method: 'PUT',
      body: JSON.stringify(uploaded),
    })
  }
  return request(base, {
    method: 'POST',
    body: JSON.stringify({ ...uploaded, filename: file.name, ...(folder ? { folder } : {}) }),
  })
}

/** 在资料库页上直接放进一份文件，`folder` 给了就放进那个文件夹。撞名不覆盖，返回它最后叫什么。 */
export function uploadLibraryFile(
  shelf: LibraryShelf,
  file: File,
  folder?: string,
  progress?: UploadProgress
): Promise<{ path: string; bytes: number }> {
  return libraryUpload(shelf, file, undefined, folder, progress)
}

/** 把一份资料或一个文件夹改名、挪到别处：`to` 是它的新名字。引用旧名字的消息跟着改。 */
export function moveLibraryFile(shelf: LibraryShelf, path: string, to: string): Promise<unknown> {
  return request(`${libraryBase(shelf)}/move`, {
    method: 'POST',
    body: JSON.stringify({ path, to }),
  })
}

/** 把一份资料换成新版本：名字不变，旧的那一份留着。 */
export function replaceLibraryFile(
  shelf: LibraryShelf,
  path: string,
  file: File,
  progress?: UploadProgress
): Promise<{ path: string; bytes: number }> {
  return libraryUpload(shelf, file, path, undefined, progress)
}

/** 一份资料的字节，给页面预览；Office 文档可以转成 PDF 再给。 */
export async function libraryFileBytes(shelf: LibraryShelf, path: string, asPdf = false): Promise<ArrayBuffer> {
  const response = await fetch(libraryFileRawUrl(shelf, path) + (asPdf ? '&preview_pdf=true' : ''), {
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
export async function listLibraryVersions(shelf: LibraryShelf, path: string): Promise<LibraryVersion[]> {
  const out = await request<{ versions: (Omit<LibraryVersion, 'added_at'> & { created_at: string | null })[] }>(
    `${libraryBase(shelf)}/versions?path=${encodeURIComponent(path)}`
  )
  return out.versions.map(({ created_at, ...rest }) => ({ ...rest, added_at: created_at }))
}

/** 某一版的字节地址（给 `downloadFile`）。 */
export function libraryVersionRawUrl(shelf: LibraryShelf, path: string, versionId: string): string {
  return `${libraryFileRawUrl(shelf, path)}&version=${encodeURIComponent(versionId)}`
}

/** 把旧的一版恢复成现在这一份：复制成新的一版，历史只增不减。 */
export function restoreLibraryVersion(shelf: LibraryShelf, path: string, versionId: string): Promise<unknown> {
  const query = `path=${encodeURIComponent(path)}&version=${encodeURIComponent(versionId)}`
  return request(`${libraryBase(shelf)}/restore?${query}`, { method: 'POST' })
}

/** 团队的资料库占了多少、一共能放多少（字节）。 */
export function libraryRoom(team: number): Promise<{ used: number; total: number }> {
  return request(`${libraryBase({ team })}/room`)
}
