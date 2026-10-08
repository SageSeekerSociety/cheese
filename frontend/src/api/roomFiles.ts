import type { ApiEnvelope, ChatAttachment, DocumentRevision, FileSource, ListPayload } from '../cx_types'
import type { DocumentTemplate, RoomOutput } from '../types/roomOutput'

import { t } from '../i18n'
import { refusalWords } from '../lib/noticeText'
import { createPreviewPdfReader } from '../lib/previewPdf'
import { postFormWithProgress } from '../lib/xhrUpload'

import { authHeaders, BASE, request } from './http'

// ---- 这个房间里摆出来的东西 (#1085 结论四) ----

export function listRoomOutputs(topicId: string): Promise<ListPayload<RoomOutput>> {
  return request<ListPayload<RoomOutput>>(`/topics/${encodeURIComponent(topicId)}/shown`)
}

/** 把房间里的这一份留进资料库：按原名，撞名加 `(2)`，所有房间都引用得到。 */
export function saveRoomOutputToLibrary(topicId: string, path: string): Promise<{ name: string }> {
  return request(`/topics/${encodeURIComponent(topicId)}/shown/save`, {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}

// ---- Chat attachments ----

/** 把资料库里已有的一份文件附在这条消息上。返回的形状和一次上传相同。 */
export async function attachLibraryFile(topicId: string, libraryPath: string): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('library_path', libraryPath)
  const res = await fetch(`${BASE}/topics/${encodeURIComponent(topicId)}/attachments`, {
    method: 'POST',
    body: form,
    headers: authHeaders(),
  })
  const envelope = (await res.json().catch(() => null)) as ApiEnvelope<ChatAttachment> | null
  if (!res.ok || !envelope || envelope.code !== 200) {
    throw new Error(refusalWords(envelope) || t('global.request.addFailed', { status: res.status }))
  }
  return envelope.data
}

// Upload a file into the project's 资料库, with a copy in this room.
//
// The body goes up over XHR so the composer can draw a determinate bar from its
// progress — `fetch` has no upload-progress event (see lib/xhrUpload.ts).
//
// `origin: 'clipboard'` 的那一份只留在这个房间：贴进来的截图没有名字（`image.png`
// 是浏览器编的），而资料库是按名字寻址的 —— 见 attachments.ts 里给它现起的名字。
export async function uploadAttachment(
  topicId: string,
  file: File,
  origin: 'file' | 'clipboard' = 'file',
  onProgress?: (fraction: number) => void
): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('file', file)
  form.append('origin', origin)
  const url = `${BASE}/topics/${encodeURIComponent(topicId)}/attachments`
  const { status, body } = await postFormWithProgress<ApiEnvelope<ChatAttachment>>(url, form, authHeaders(), onProgress)
  if (status < 200 || status >= 300 || !body || body.code !== 200)
    throw new Error(refusalWords(body) || t('global.request.uploadFailed', { status }))
  return body.data
}

// <img src=…> URL for an uploaded attachment (binary raw endpoint).
//
// 注意这不是一个可以挂在 <img src> 上的地址：raw 端点从 Authorization 头认人，
// 而浏览器发图片请求时带不了头（也读不到 localStorage）。挂上去的结果是 401，
// 读者看到的是一张裂图。要显示图片用下面的 attachmentImageUrl。
/** `task` 说的是从哪个库读：某个任务工作树上的那一份，还是房间自己的文件（不传）。
 *  同一个路径在两个库里可以是两份不同的文件，所以看谁的文件必须说出来。 */
export function attachmentRawUrl(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): string {
  const from = task ? `&task=${encodeURIComponent(task)}` : ''
  return `${BASE}/topics/${encodeURIComponent(topicId)}/attachments/raw?path=${encodeURIComponent(path)}${from}&source=${source}`
}

/** 图片附件的字节，取回来做成 <img> 能用的 object URL。
 *
 * 先 fetch 再转 URL 不是为了多走一步，是因为只有 fetch 才能带上 Authorization：
 * 这个端点不接受匿名请求，而 `<img src="/api/…/attachments/raw?path=…">` 恰恰
 * 是匿名的。调用方负责在不再需要时 URL.revokeObjectURL（见 AttachmentImage）。
 */
export async function attachmentImageUrl(topicId: string, path: string): Promise<string> {
  const res = await fetch(attachmentRawUrl(topicId, path), { headers: authHeaders() })
  if (!res.ok) throw new Error(t('global.request.imageFailed', { status: res.status }))
  return URL.createObjectURL(await res.blob())
}

/** A published file's bytes, for a viewer that draws them in the page. */
export async function previewFileBytes(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<ArrayBuffer> {
  // `download=true` is what makes the raw endpoint serve a non-image at all; it
  // only changes the Content-Disposition, which nothing here reads.
  const res = await fetch(`${attachmentRawUrl(topicId, path, task, source)}&download=true`, {
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(t('global.request.fileReadFailed', { status: res.status }))
  return res.arrayBuffer()
}

/** 一份 .docx 里的修订，按读者读到的顺序。
 *
 *  旁边那份 PDF 已经把改动画出来了（LibreOffice 会渲染修订：插入带下划线、删除带
 *  删除线）。这份清单不是为了让人看见改动，是为了让人**处理**改动——不打开 Word 就能
 *  逐条接受或拒绝。 */
export function documentRevisions(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<{ path: string; version: string; revisions: DocumentRevision[] }> {
  const query = `?path=${encodeURIComponent(path)}&source=${source}` + (task ? `&task=${encodeURIComponent(task)}` : '')
  return request<{ path: string; version: string; revisions: DocumentRevision[] }>(
    `/topics/${encodeURIComponent(topicId)}/documents/revisions${query}`
  )
}

/** 接受或拒绝其中几处，写回文件，返回剩下的那些。
 *
 *  序号对应的是调用方刚拿到的那份清单。处理完之后剩下的会重新从 1 数起，所以调用方
 *  要用返回的这份清单替换手上那份，不能接着用旧序号。
 *
 *  `version` 是读这份清单时那份文件的版本。芝士在这中间重新交付过这个文件时，这次处理
 *  会被拒绝而不是把新的那份盖掉。 */
export function decideDocumentRevisions(
  topicId: string,
  path: string,
  version: string,
  decision: { accept?: number[]; reject?: number[] },
  task?: string | null
): Promise<{ path: string; version: string; revisions: DocumentRevision[] }> {
  return request<{ path: string; version: string; revisions: DocumentRevision[] }>(
    `/topics/${encodeURIComponent(topicId)}/documents/revisions`,
    { method: 'POST', body: JSON.stringify({ path, version, task: task ?? undefined, ...decision }) }
  )
}

/** Bytes and source fingerprint from the same authorized conversion response. */
export const previewDocumentPdfSnapshot = createPreviewPdfReader(BASE, authHeaders)

/** A Word or PowerPoint file converted to PDF, so a browser can draw it. */
export async function previewDocumentPdf(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<ArrayBuffer> {
  return (await previewDocumentPdfSnapshot(topicId, path, task, source)).bytes
}

// Downloads carry the same credentials as API requests, including token-only sessions.
export async function downloadFile(rawUrl: string, filename: string): Promise<void> {
  const res = await fetch(`${rawUrl}${rawUrl.includes('?') ? '&' : '?'}download=true`, { headers: authHeaders() })
  if (!res.ok) throw new Error(t('global.request.downloadFailed', { status: res.status }))
  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ---- 房间文件：草稿历史与在线编辑 ----

/** 房间文件保存过的一版。`source` 说字节从哪条路进来：编辑器、芝士、恢复、上传。 */
export interface RoomFileRevision {
  id: string
  path: string
  seq: number
  version: string
  size: number
  author: string
  author_kind: 'human' | 'agent' | 'unknown'
  source: 'baseline' | 'upload' | 'ai' | 'editor' | 'restore' | 'scheduled'
  note: string | null
  /** 存下这一版的那次编辑会话；和自己打开时的那个相同，就是自己刚存的。 */
  editor_key: string | null
  created_at: string
}

export function roomFileRevisions(
  topicId: string,
  path: string
): Promise<{ data: RoomFileRevision[]; total: number; path: string; version: string | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/revisions?path=${encodeURIComponent(path)}`)
}

export function restoreRoomFileRevision(topicId: string, revisionId: string): Promise<RoomFileRevision> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revisionId)}/restore`, {
    method: 'POST',
  })
}

export async function downloadRoomFileRevision(topicId: string, revision: RoomFileRevision): Promise<void> {
  const res = await fetch(
    `${BASE}/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revision.id)}/raw`,
    { headers: authHeaders() }
  )
  if (!res.ok) throw new Error(t('global.request.downloadFailed', { status: res.status }))
  const blob = await res.blob()
  const leaf = revision.path.split('/').pop() ?? 'file'
  const dot = leaf.lastIndexOf('.')
  const [base, ext] = dot > 0 ? [leaf.slice(0, dot), leaf.slice(dot)] : [leaf, '']
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = t('global.request.revisionFileName', { name: base, seq: revision.seq, ext })
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function copyIntoRoom(
  topicId: string,
  source: string,
  path: string
): Promise<{ path: string; version: string }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/copy`, {
    method: 'POST',
    body: JSON.stringify({ source, path }),
  })
}

/** 打开编辑器要的那份签过名的配置。`enabled` 为假时 `reason` 是为什么打不开的码。 */
export interface RoomFileEditorSession {
  enabled: boolean
  reason?: 'not_configured' | 'unsupported' | 'library_original'
  copyable?: boolean
  editable?: boolean
  api_url?: string
  version?: string
  config?: Record<string, unknown>
}

export function openRoomFileEditor(topicId: string, path: string): Promise<RoomFileEditorSession> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/editor?path=${encodeURIComponent(path)}`)
}

export function listDocumentTemplates(topicId: string): Promise<{ data: DocumentTemplate[]; total: number }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/templates`)
}

export function newFromTemplate(
  topicId: string,
  template: string,
  path: string
): Promise<{ path: string; version: string }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/new`, {
    method: 'POST',
    body: JSON.stringify({ template, path }),
  })
}
