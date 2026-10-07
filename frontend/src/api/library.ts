import type { ListPayload } from '../cx_types'
import type { LibraryFile } from '../lib/libraryApi'

import { BASE, request } from './http'

export type { LibraryFile }

export function listProjectLibrary(projectId: string): Promise<ListPayload<LibraryFile>> {
  return request<ListPayload<LibraryFile>>(`/projects/${encodeURIComponent(projectId)}/library`)
}

/** 一份资料的字节。这条端点一律按下载发，所以 `downloadFile` 补在末尾的
 *  `download=true` 在这里没有对应的参数，后端不看它。 */
export function libraryFileRawUrl(projectId: string, path: string): string {
  return `${BASE}/projects/${encodeURIComponent(projectId)}/library/raw?path=${encodeURIComponent(path)}`
}

/** 题目附件清单里那份材料的字节。清单本身走 `TasksApi.listAttachments`，但那条
 *  接口故意不带 url（存储给的是直链，发出来就绕过了下载那道门），所以取文件只能
 *  从这个端点走 —— 门在服务端，带的是这次请求自己的 Authorization。
 *  同 `libraryFileRawUrl`：`downloadFile` 补的 `download=true` 这里没有对应参数。 */
export function taskAttachmentRawUrl(taskId: number, attachmentId: number): string {
  return `${BASE}/tasks/${taskId}/attachments/${attachmentId}/download`
}

export function deleteLibraryFile(projectId: string, path: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/library?path=${encodeURIComponent(path)}`,
    { method: 'DELETE' }
  )
}
