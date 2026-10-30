import type { LibraryFile } from '../lib/libraryApi'

import { BASE, request } from './http'

export type { LibraryFile }

/** 哪一个资料库：给项目编号就是那个项目的；团队的（一个人自己的是他名下那个团队的）
 *  写成 `{ team: 团队 id }`。 */
export type LibraryShelf = string | { team: number }

/** 这个资料库的接口地址（不带 `BASE`）。 */
export function libraryBase(shelf: LibraryShelf): string {
  return typeof shelf === 'string' ? `/projects/${encodeURIComponent(shelf)}/library` : `/teams/${shelf.team}/library`
}

/** 一份资料的字节。这条端点一律按下载发，所以 `downloadFile` 补在末尾的
 *  `download=true` 在这里没有对应的参数，后端不看它。 */
export function libraryFileRawUrl(shelf: LibraryShelf, path: string): string {
  return `${BASE}${libraryBase(shelf)}/raw?path=${encodeURIComponent(path)}`
}

export function deleteLibraryFile(shelf: LibraryShelf, path: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`${libraryBase(shelf)}?path=${encodeURIComponent(path)}`, {
    method: 'DELETE',
  })
}
