import type {
  FileContent,
  FileSource,
  GitCommit,
  ListPayload,
  PreviewInfo,
  TopicWorkSummary,
  UsageStats,
  WorkspaceFile,
} from '../cx_types'
import type { SitePage } from '../types/site'

import { BASE, request } from './http'

// ---- 执行面板 (Phase 4 tool drawers) ----

// 现场 (施工现场): a topic's tool/event record (read-only), newest window first.
// Paged: events are the most numerous kind of block (one per tool call), so an
// unpaged 现场 is the largest request the app can make and it only grows.
//
// `author` narrows a page to one teammate's steps — a room can seat several, and
// 现场 reads them one at a time. It narrows the query, not the page: the filter
// runs inside the paging (same as the backend's `kinds`), so a page still holds
// `limit` rows and `has_more` is about what is left for THAT teammate.
export const SITE_PAGE_SIZE = 120
export function getTranscript(
  topicId: string,
  opts: { limit?: number; before?: string; author?: string | null } = {}
): Promise<SitePage> {
  const q = new URLSearchParams()
  if (opts.limit != null) q.set('limit', String(opts.limit))
  if (opts.before) q.set('before', opts.before)
  if (opts.author) q.set('author', opts.author)
  const qs = q.toString()
  return request<SitePage>(`/topics/${encodeURIComponent(topicId)}/transcript${qs ? `?${qs}` : ''}`)
}

// 现场一步打印出来的东西：后端只留末尾一截（至多 8 KiB，凭据已抹掉）。列表和
// socket 上只带它有多长（`meta.output_bytes`），摊开那一步时才来取这一份。
export function getStepOutput(topicId: string, blockId: string): Promise<{ output: string; bytes: number }> {
  return request<{ output: string; bytes: number }>(
    `/topics/${encodeURIComponent(topicId)}/transcript/${encodeURIComponent(blockId)}/output`
  )
}

export function getGitLog(
  projectId: string,
  topicId?: string | null,
  taskId?: string | null
): Promise<ListPayload<GitCommit>> {
  const t = `?${new URLSearchParams({ ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<ListPayload<GitCommit>>(`/projects/${encodeURIComponent(projectId)}/git/log${t}`)
}

export function getGitDiff(
  projectId: string,
  topicId?: string | null,
  taskId?: string | null,
  source: FileSource = 'committed'
): Promise<{ diff: string }> {
  const t = `?${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<{ diff: string }>(`/projects/${encodeURIComponent(projectId)}/git/diff${t}`)
}

// 工作面板 asks this while its tabs are CLOSED: which of them have anything to
// show, and what count belongs on 改动. Both are facts about tabs nobody is
// looking at, so neither may cost what opening the tab costs.
export function getTopicWorkSummary(projectId: string, topicId: string): Promise<TopicWorkSummary> {
  const p = encodeURIComponent(projectId)
  return request<TopicWorkSummary>(`/projects/${p}/topics/${encodeURIComponent(topicId)}/work-summary`)
}

// 文件: list workspace files; read one file's content.
export function listFiles(
  projectId: string,
  topicId?: string | null,
  taskId?: string | null,
  source: FileSource = 'live'
): Promise<ListPayload<WorkspaceFile> & { source: FileSource }> {
  const t = `?${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<ListPayload<WorkspaceFile> & { source: FileSource }>(
    `/projects/${encodeURIComponent(projectId)}/files${t}`
  )
}

// <img src=…> URL for a workspace file (binary raw endpoint) — the 文件 panel
// shows images as images instead of Monaco-mangled bytes.
export function workspaceFileRawUrl(
  projectId: string,
  path: string,
  topicId?: string,
  taskId?: string | null,
  source: FileSource = 'live'
): string {
  const t = `&${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return `${BASE}/projects/${encodeURIComponent(projectId)}/file/raw?path=${encodeURIComponent(path)}${t}`
}

export function readFile(
  projectId: string,
  path: string,
  topicId?: string | null,
  taskId?: string | null,
  source: FileSource = 'live'
): Promise<FileContent> {
  const t = `&${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<FileContent>(`/projects/${encodeURIComponent(projectId)}/file?path=${encodeURIComponent(path)}${t}`)
}

// Save an edited workspace file (人改文件即指令). The agent reads the latest on
// its next turn, like 改文档即指令.
//
// `version` is the one readFile returned. Sending it makes the write
// conditional: if 芝士 wrote the same file in between, the backend answers 409
// instead of letting this save erase their edits without a trace.
export function writeFile(
  projectId: string,
  path: string,
  content: string,
  topicId?: string | null,
  version?: string | null,
  taskId?: string | null
): Promise<{ path: string; version: string }> {
  const t = `?${new URLSearchParams({ ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request(`/projects/${encodeURIComponent(projectId)}/file${t}`, {
    method: 'PUT',
    body: JSON.stringify({ path, content, version: version ?? null }),
  })
}

// 预览 (spec §9.1): the artifact 芝士 pointed at as the topic's current preview,
// or null if none is set. Content is fetched separately via readFile.
export function getPreview(topicId: string): Promise<PreviewInfo | null> {
  return request<PreviewInfo | null>(`/topics/${encodeURIComponent(topicId)}/preview`)
}

/** 预览正在显示的那份文件，或者房间里指名的某一份。
 *
 *  消息里的 `<&路径>` 只是一个路径，不带它在哪个库。房间自己的文件不在任何分支上，
 *  所以按路径读这里，是从那枚 chip 走到那份文件的唯一一条路。 */
export function readPreviewFile(topicId: string, path?: string): Promise<FileContent> {
  const query = path ? `?path=${encodeURIComponent(path)}` : ''
  return request<FileContent>(`/topics/${encodeURIComponent(topicId)}/preview/file${query}`)
}

// 资源: aggregated token/cost usage for a topic and for the whole project.
export function getTopicUsage(topicId: string): Promise<UsageStats> {
  return request<UsageStats>(`/topics/${encodeURIComponent(topicId)}/usage`)
}

export function getProjectUsage(projectId: string): Promise<UsageStats> {
  return request<UsageStats>(`/projects/${encodeURIComponent(projectId)}/usage`)
}

// 内测版本徽标: the running backend build. `badge` is the box's opt-in flag;
// `short` is the 7-char sha to show. Public, unauthenticated.
export interface AppVersion {
  sha: string
  short: string
  badge: boolean
}

export function getAppVersion(): Promise<AppVersion> {
  return request<AppVersion>('/version')
}
