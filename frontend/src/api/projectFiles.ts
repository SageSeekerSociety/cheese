// 资料库里的「项目文件」：项目当前版本里的文件，和它们是怎么一次次改成现在这样的。
//
// 文件本身走工作区那几条（`workPanel.ts` 的 `listFiles` / `workspaceFileRawUrl`，
// 不带任务、`committed`）；这里是改动记录：哪一次、谁、什么时候，来自哪件任务。
import type { ProjectChange } from '../cx_types'

import { t } from '../i18n'

import { ApiError, authHeaders, request } from './http'
import { workspaceFileRawUrl } from './workPanel'

/** 项目的改动记录，新的在前；给了 `path` 就只要改过这一份的那几次。项目还没有提交时
 *  是 404。 */
export async function listProjectChanges(
  projectId: string,
  opts: { path?: string; limit?: number } = {}
): Promise<ProjectChange[]> {
  const params = new URLSearchParams()
  if (opts.path) params.set('path', opts.path)
  if (opts.limit) params.set('limit', String(opts.limit))
  const query = params.toString()
  const out = await request<{ commits: ProjectChange[] }>(
    `/projects/${encodeURIComponent(projectId)}/history${query ? `?${query}` : ''}`
  )
  return out.commits
}

/** 项目当前版本里一份文件的字节（给预览）。 */
export async function projectFileBytes(projectId: string, path: string): Promise<ArrayBuffer> {
  const response = await fetch(workspaceFileRawUrl(projectId, path, undefined, null, 'committed'), {
    headers: authHeaders(),
  })
  if (response.ok) return response.arrayBuffer()
  let message = ''
  try {
    message = String((await response.json())?.message || '')
  } catch {
    // 不是 JSON：只剩状态码可说。
  }
  throw new ApiError(response.status, message || t('files.library.readFailed', { status: response.status }))
}
