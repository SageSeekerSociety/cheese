import type {
  FileContent,
  FileSource,
  ListPayload,
  PreviewInfo,
  PreviewSnapshot,
  PreviewWakeAnswer,
  PreviewWakeStatus,
  TopicWorkSummary,
  UsageStats,
  WorkspaceFile,
} from '../cx_types'
import type { SitePage } from '../types/site'

import { ApiError, authHeaders, BASE, request } from './http'

// ---- 执行面板 (Phase 4 tool drawers) ----

// 现场 (施工现场): a conversation's raw session records (read-only), newest
// window first. Paged: one record per harness line, so an unpaged 现场 would be
// the largest request the app can make and it only grows.
//
// `author` narrows a page to one teammate's steps — a room can seat several, and
// 现场 reads them one at a time. It narrows the query, not the page: the filter
// runs inside the paging (same as the backend's `kinds`), so a page still holds
// `limit` rows and `has_more` is about what is left for THAT teammate.
export const SITE_PAGE_SIZE = 120
export function getTranscript(
  topicId: string,
  opts: { limit?: number; before?: string; after?: string; author?: string | null } = {}
): Promise<SitePage> {
  const q = new URLSearchParams()
  if (opts.limit != null) q.set('limit', String(opts.limit))
  if (opts.before) q.set('before', opts.before)
  if (opts.after) q.set('after', opts.after)
  if (opts.author) q.set('author', opts.author)
  const qs = q.toString()
  return request<SitePage>(`/topics/${encodeURIComponent(topicId)}/transcript${qs ? `?${qs}` : ''}`)
}

export async function sessionRecordImageUrl(topicId: string, digest: string): Promise<string> {
  const response = await fetch(
    `${BASE}/topics/${encodeURIComponent(topicId)}/session-record-images/${encodeURIComponent(digest)}`,
    { headers: authHeaders() }
  )
  if (!response.ok) throw new ApiError(response.status, response.statusText)
  return URL.createObjectURL(await response.blob())
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

// Save an edited workspace file (人改文件即指令). `version` and `base` are what readFile
// returned: if the file changed in between, the backend merges both edits against `base`
// (`merged`, with the saved `content`), or answers 409 with the regions to pick from.
export function writeFile(
  projectId: string,
  path: string,
  content: string,
  topicId?: string | null,
  version?: string | null,
  taskId?: string | null,
  base?: string | null
): Promise<{ path: string; version: string; merged?: boolean; content?: string }> {
  const t = `?${new URLSearchParams({ ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request(`/projects/${encodeURIComponent(projectId)}/file${t}`, {
    method: 'PUT',
    body: JSON.stringify({ path, content, version: version ?? null, base: base ?? null }),
  })
}

// 预览 (spec §9.1): the artifact 芝士 pointed at as the topic's current preview,
// or null if none is set. Content is fetched separately via readFile.
export function getPreview(topicId: string): Promise<PreviewInfo | null> {
  return request<PreviewInfo | null>(`/topics/${encodeURIComponent(topicId)}/preview`)
}

/** 预览里那个应用最近一次成功加载时留的静态快照；从没留过是 null。 */
export function getPreviewSnapshot(topicId: string): Promise<PreviewSnapshot | null> {
  return request<PreviewSnapshot | null>(`/topics/${encodeURIComponent(topicId)}/preview/snapshot`)
}

/** 留下预览里那个应用此刻的快照（帧里的运行时桥拍的）。`artifactId` 是拍的时候摆着的那一件。 */
export function putPreviewSnapshot(
  topicId: string,
  artifactId: string,
  shot: { html: string; href: string; title: string }
): Promise<PreviewSnapshot> {
  return request<PreviewSnapshot>(`/topics/${encodeURIComponent(topicId)}/preview/snapshot`, {
    method: 'PUT',
    body: JSON.stringify({ artifact_id: artifactId, ...shot }),
  })
}

/** 请声明这个应用的队友把它重新跑起来。没起新的一轮时 `reason` 说为什么。 */
export function wakePreviewApp(topicId: string): Promise<PreviewWakeAnswer> {
  return request<PreviewWakeAnswer>(`/topics/${encodeURIComponent(topicId)}/preview/wake`, { method: 'POST' })
}

/** 唤醒那几步此刻走到哪了。 */
export function getPreviewWakeStatus(topicId: string): Promise<PreviewWakeStatus> {
  return request<PreviewWakeStatus>(`/topics/${encodeURIComponent(topicId)}/preview/wake-status`)
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
