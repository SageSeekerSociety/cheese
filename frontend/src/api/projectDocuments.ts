// 项目自己的文档：资料库里那些能一起改的文档。对话自带的那一份不在列表里，
// 但资料库的搜索找得到它，可以另存一份进来。
import type { DocumentSearch, ProjectDocument } from '../lib/projectDocument'

import { t } from '../i18n'
import { rememberNumbered } from '../lib/addresses'
import { refusalWords } from '../lib/noticeText'

import { authHeaders, BASE, request } from './http'

export type { DocumentHit, DocumentSearch, ProjectDocument } from '../lib/projectDocument'

const base = (projectId: string) => `/projects/${encodeURIComponent(projectId)}/documents`
const one = (documentId: string) => `/documents/${encodeURIComponent(documentId)}`

/** 列表里的一份文档，带着它在资料库页那张混排的表里的位置（后端 `app/core/rank.py`）。 */
export type ListedDocument = ProjectDocument & { rank: string }

/** 项目自己的文档，一页一页地给，最近改过的在前；`next` 是下一页的游标。 */
export function listProjectDocuments(
  projectId: string,
  cursor: string | null = null,
  limit = 50
): Promise<{ data: ListedDocument[]; next: string | null }> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (cursor) params.set('cursor', cursor)
  return request<{ data: ListedDocument[]; next: string | null }>(`${base(projectId)}?${params}`).then((page) => {
    // 项目的文档都带着它的项目；团队自己的文档不在这一页里，也没有编号可记。
    rememberNumbered(
      'docs',
      page.data.flatMap((doc) => (doc.project_id ? [{ ...doc, project_id: doc.project_id }] : []))
    )
    return page
  })
}

/** 新建一份：空的，或者另存 `copyOf` 那一份现在的样子。 */
export function createProjectDocument(
  projectId: string,
  body: { title?: string; copy_of?: string } = {}
): Promise<ProjectDocument> {
  return request<ProjectDocument>(base(projectId), { method: 'POST', body: JSON.stringify(body) })
}

export function searchProjectDocuments(projectId: string, q: string): Promise<DocumentSearch> {
  return request<DocumentSearch>(`${base(projectId)}/search?q=${encodeURIComponent(q)}`)
}

/** 这份文档叫什么、是谁的；还没写过字的也答得出。 */
export function getDocumentAbout(documentId: string): Promise<ProjectDocument> {
  return request<ProjectDocument>(`${one(documentId)}/about`)
}

export function renameDocument(documentId: string, title: string): Promise<ProjectDocument> {
  return request<ProjectDocument>(one(documentId), { method: 'PATCH', body: JSON.stringify({ title }) })
}

export function deleteDocument(documentId: string): Promise<unknown> {
  return request(one(documentId), { method: 'DELETE' })
}

/** 文档里引用的一张图（`library/<名字>`，或任务文档所在频道里的文件），取回来做成
 *  `<img>` 能用的 object URL。读得了这份文档的人才拿得到。
 *
 *  先 fetch 再转 URL，是因为只有 fetch 带得上登录凭证，`<img src>` 发出的请求是匿名的。
 *  调用方在不再需要时 `URL.revokeObjectURL`。 */
export async function documentImageUrl(documentId: string, path: string): Promise<string> {
  return URL.createObjectURL(await documentImageBytes(documentId, path))
}

/** 印成纸用的那张图：字节就在地址里（`data:`），平台的打印浏览器没有凭证也画得出来。 */
export async function documentImageDataUrl(documentId: string, path: string): Promise<string> {
  return await bytesAsDataUrl(await documentImageBytes(documentId, path))
}

async function documentImageBytes(documentId: string, path: string): Promise<Blob> {
  const res = await fetch(`${BASE}${one(documentId)}/image?path=${encodeURIComponent(path)}`, {
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(t('global.request.imageFailed', { status: res.status }))
  return await res.blob()
}

async function bytesAsDataUrl(blob: Blob): Promise<string> {
  const shown = await smaller(blob).catch(() => blob)
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('unreadable'))
    reader.readAsDataURL(shown)
  })
}

/** 长边缩到 1600 像素：A4 的正文列约 660 像素宽，打出 200dpi 也就这么多，再大只是让
 *  那一页变重。缩不了、缩完更大、浏览器不给画布时，原样还回去。 */
async function smaller(blob: Blob): Promise<Blob> {
  if (blob.size <= 300 * 1024 || typeof createImageBitmap !== 'function') return blob
  const bitmap = await createImageBitmap(blob)
  try {
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height))
    if (scale === 1 && blob.size <= 800 * 1024) return blob
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const context = canvas.getContext('2d')
    if (!context) return blob
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const made = await new Promise<Blob | null>((resolve) => canvas.toBlob((out) => resolve(out), 'image/webp', 0.9))
    return made && made.size < blob.size ? made : blob
  } finally {
    bitmap.close()
  }
}

/** 把排好的一页交给平台的打印浏览器，回执是那份 PDF。读得了这份文档的人才拿得到；
 *  部署里没有打印浏览器时，后端用一句现成的话拒绝。 */
export async function documentPdf(documentId: string, html: string): Promise<Blob> {
  const res = await fetch(`${BASE}${one(documentId)}/pdf`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ html }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(refusalWords(body) || t('work.room.doc.exportPdfFailed'))
  }
  return await res.blob()
}

/** 这份文档最后存下的正文；还没写过字时是空的。 */
export async function getDocumentText(documentId: string): Promise<string> {
  const stored = await request<{ content: string } | null>(one(documentId))
  return stored?.content ?? ''
}

/** 项目总览是哪一份文档（第一次问时建出空的一份）。综合的概览和项目文档页都显示它。 */
export function getProjectOverview(projectId: string): Promise<{ id: string }> {
  return request<{ id: string }>(`/projects/${encodeURIComponent(projectId)}/overview`)
}
