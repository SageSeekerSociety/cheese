import type { ApiEnvelope, ChatAttachment, DocumentRevision, FileSource, ListPayload } from '../cx_types'
import type { DocumentTemplate, RoomOutput } from '../types/roomOutput'

import { t } from '../i18n'
import { announceDownload, announceDownloadFailure, saveBlob } from '../lib/download'
import { refusalWords } from '../lib/noticeText'
import { createPreviewPdfReader } from '../lib/previewPdf'
import { postFormWithProgress } from '../lib/xhrUpload'

import { authHeaders, BASE, downloadBytes, ensureFreshToken, request } from './http'

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

// ---- 这段对话里的附件和文档 (#3828) ----

/** 这段对话里发过的一份附件。`in_library` 说它在不在资料库里。 */
export interface RoomAttachment {
  path: string
  name: string
  mime: string | null
  added_by: string
  added_at: string
  in_library: boolean
}

/** 芝士在这段对话里写的一份文档。 */
export interface RoomDocument {
  id: string
  number: number | null
  title: string | null
  author: string
  created_at: string
  in_library: boolean
}

/** 这个频道或私聊里发过的附件（连同它的任务和支线），新的在前。 */
export function listRoomAttachments(topicId: string): Promise<ListPayload<RoomAttachment>> {
  return request<ListPayload<RoomAttachment>>(`/topics/${encodeURIComponent(topicId)}/attachments`)
}

/** 芝士在这个频道或私聊里写的文档，新的在前。 */
export function listRoomDocuments(topicId: string): Promise<ListPayload<RoomDocument>> {
  return request<ListPayload<RoomDocument>>(`/topics/${encodeURIComponent(topicId)}/documents`)
}

/** 把一份文档保存到资料库：项目里的人从此都能看到它。 */
export function keepDocument(documentId: string): Promise<RoomDocument> {
  return request<RoomDocument>(`/documents/${encodeURIComponent(documentId)}/keep`, { method: 'POST' })
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

// Upload a file into this room: it stays in the conversation until someone saves
// it to the 资料库 (#3828).
//
// The body goes up over XHR so the composer can draw a determinate bar from its
// progress — `fetch` has no upload-progress event (see lib/xhrUpload.ts).
export async function uploadAttachment(
  topicId: string,
  file: File,
  onProgress?: (fraction: number) => void
): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('file', file)
  const url = `${BASE}/topics/${encodeURIComponent(topicId)}/attachments`
  const { status, body } = await postFormWithProgress<ApiEnvelope<ChatAttachment>>(url, form, authHeaders(), onProgress)
  if (status < 200 || status >= 300 || !body || body.code !== 200)
    throw new Error(refusalWords(body) || t('global.request.uploadFailed', { status }))
  return body.data
}

/** 输入框里说的一段：交给后端听写，回来整理好的字（`text`）和原话（`raw`）。 */
export async function hearVoice(topicId: string, wav: Blob): Promise<{ text: string; raw: string }> {
  const form = new FormData()
  form.append('file', wav, 'voice.wav')
  const url = `${BASE}/topics/${encodeURIComponent(topicId)}/voice`
  const { status, body } = await postFormWithProgress<ApiEnvelope<{ text: string; raw: string }>>(
    url,
    form,
    authHeaders()
  )
  if (status < 200 || status >= 300 || !body || body.code !== 200)
    throw new Error(refusalWords(body) || t('global.request.uploadFailed', { status }))
  return body.data
}

/** 对自己的芝士说的一段：不在哪个频道里，听写的方式和额度都和频道里的一样。 */
export async function hearAssistantVoice(wav: Blob): Promise<{ text: string; raw: string }> {
  const form = new FormData()
  form.append('file', wav, 'voice.wav')
  const { status, body } = await postFormWithProgress<ApiEnvelope<{ text: string; raw: string }>>(
    `${BASE}/assistant/voice`,
    form,
    authHeaders()
  )
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
export function attachmentImageUrl(topicId: string, path: string): Promise<string> {
  return rawImageUrl(attachmentRawUrl(topicId, path))
}

/** 任何一个只认 Authorization 的 raw 端点（房间附件、项目文件），取成 <img> 能用的
 *  object URL。理由同上：`<img src>` 是匿名的，项目的 `/file/raw` 对它答 404。 */
export async function rawImageUrl(rawUrl: string): Promise<string> {
  // `request()` refreshes a token about to expire before it sends; a bare fetch has
  // to do the same, or an image opened after the tab sat idle gets 404.
  await ensureFreshToken()
  const res = await fetch(rawUrl, { headers: authHeaders() })
  if (!res.ok) throw new Error(t('global.request.imageFailed', { status: res.status }))
  return URL.createObjectURL(await res.blob())
}

/** The address a `<video>` plays a video attachment from, streaming it, and how
 *  many bytes the whole recording is (from the one range request below).
 *
 *  The element fetches the recording itself, one byte range at a time, and none
 *  of those requests can carry Authorization. So this reads the first byte with
 *  the header, and the response leaves a cookie scoped to this room's raw path
 *  (backend `remember_for_media`); the element's own requests to the same URL
 *  carry it. Nothing is downloaded up front, so a long recording starts playing
 *  at once and seeking reads only the part it lands on. The same response's
 *  `Content-Range` carries the total size, so the card can say how big the file
 *  is without a second request. */
export async function attachmentMediaUrl(
  topicId: string,
  path: string
): Promise<{ url: string; bytes: number | null }> {
  const url = attachmentRawUrl(topicId, path)
  // no-store: a cached answer would skip the response that renews the cookie,
  // and once the old one expires the player's own range requests get 401.
  const res = await fetch(url, {
    headers: { ...authHeaders(), Range: 'bytes=0-0' },
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(t('global.request.fileReadFailed', { status: res.status }))
  await res.body?.cancel()
  const total = Number(res.headers.get('Content-Range')?.split('/').at(-1))
  return { url, bytes: Number.isFinite(total) && total > 0 ? total : null }
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

/** 一份演示稿每一页的演讲者备注。
 *
 *  备注不在幻灯片上，而这一格拿到的是**转换后的 PDF**——所以它是这一档唯一读不出来的
 *  东西。从服务端要，是因为那一边本来就有这份 pptx 的字节（正在转它）；让浏览器为了
 *  几 KB 的文本再下一次原稿（上限 200MB）不值当。
 *
 *  `readable` 为假是「这份不是读得开的演示稿」；`notes` 里那一页是空串是「这一页没有
 *  备注」。两件事分开说，否则一份读不开的文件会和一份没有备注的稿子长得一样。 */
export function previewSlideNotes(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<{ notes: string[]; readable: boolean; version: string }> {
  const from = task ? `&task=${encodeURIComponent(task)}` : ''
  return request<{ notes: string[]; readable: boolean; version: string }>(
    `/topics/${encodeURIComponent(topicId)}/attachments/notes?path=${encodeURIComponent(path)}&source=${source}${from}`
  )
}

// Downloads carry the same credentials as API requests, including token-only sessions.
//
// 成没成、失败了怎么说，都在 `lib/download` 那一层定：这里只负责带凭证取字节、把
// 字节交给浏览器，下完说一句话。所有下载入口都汇到这一个函数上（附件、资料库、预览、
// 验收交付物、产物、任务文件、项目导出…），所以成功提示和失败提示各写一次就够。
export async function downloadFile(rawUrl: string, filename: string): Promise<void> {
  try {
    const bytes = await downloadBytes(`${rawUrl}${rawUrl.includes('?') ? '&' : '?'}download=true`, authHeaders())
    saveBlob(bytes, filename)
    announceDownload()
  } catch (cause) {
    // 说过一次就不再往上抛：调用点各自那行红字已经撤掉，再抛一次没人接，只会多一条
    // 没人看的 unhandled rejection。
    announceDownloadFailure(cause)
  }
}

// ---- 房间文件：在线编辑（草稿历史在 ./roomFileRevisions） ----

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
  reason?: 'not_configured' | 'unsupported' | 'library_original' | 'attachment_original'
  copyable?: boolean
  editable?: boolean
  api_url?: string
  version?: string
  config?: Record<string, unknown>
}

export function openRoomFileEditor(topicId: string, path: string): Promise<RoomFileEditorSession> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/editor?path=${encodeURIComponent(path)}`)
}

// ---- 这个房间自己的文件 ----

/** 房间文件区里的一份文件：路径、大小、最后一次改动的时间，和最后是谁从哪条路放下的。
 *
 *  `author`／`author_kind`／`source` 是平台按写下来的那次动作记的（谁按的保存、谁交
 *  的），不是谁自己填的。三个都可能空：这一页列的是目录本身，而目录里的字节可以由别的
 *  东西直接写进去，那种文件说不出是谁写的。`author_kind` 为 `agent` 时 `author` 是队友
 *  的 handle；`baseline` 那一版不署人（`author` 空、`author_kind` 是 `unknown`）。 */
export interface RoomFile {
  path: string
  bytes: number
  /** ISO 时刻，照 `relTime` 吃的样子给。 */
  modified_at: string
  author: string | null
  author_kind: 'human' | 'agent' | 'unknown' | null
  /** 字节从哪条路进来：`upload`、`ai`、`editor`、`restore`、`scheduled`…… */
  source: string | null
  /** 有人从这里把它保存到了资料库。 */
  in_library: boolean
}

/** 这个房间的文件区：它自己的东西都在这一处——芝士交付的文档、定时任务交回的结果、有
 *  人放进来的文件。最近改的在最前；`truncated` 为真时说清单不是全部。 */
export function listRoomFiles(topicId: string): Promise<{ data: RoomFile[]; total: number; truncated: boolean }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files`)
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

/** 把一份文本房间文件的新内容存回去。
 *
 *  `version` 是读它时那份内容给的版本；这份文件在那之后被别人改过，后端答 409（带上
 *  现在那一版），而不是把这版直接盖上去——别人的改动不能无声地没了。 */
export function writeRoomTextFile(
  topicId: string,
  path: string,
  content: string,
  version: string | null
): Promise<{ path: string; version: string; seq: number }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/text`, {
    method: 'POST',
    body: JSON.stringify({ path, content, version }),
  })
}
