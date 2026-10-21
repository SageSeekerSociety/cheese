// 文档的分享链接（#3774）：拿到链接的人不用登录就能读这份文档的当前版本，看不到评论、
// 修改建议和修改记录。一份文档最多一条；停止分享就作废，再分享是一条新链接。
import type { DocApp, DocAppSource, DocAppVersion } from '../lib/docApps'

import { BASE, request } from './http'

export interface ShareLink {
  token: string
  created_by: string
  created_at: string
}

export interface DocumentShare {
  link: ShareLink | null
  /** 自己能不能创建、停止这条链接。 */
  may_share: boolean
}

const one = (documentId: string) => `/documents/${encodeURIComponent(documentId)}/share`

export function getDocumentShare(documentId: string): Promise<DocumentShare> {
  return request<DocumentShare>(one(documentId))
}

/** 创建链接；已经有了就是那一条。 */
export function shareDocument(documentId: string): Promise<DocumentShare> {
  return request<DocumentShare>(one(documentId), { method: 'PUT' })
}

/** 停止分享：这条链接立刻失效。 */
export function unshareDocument(documentId: string): Promise<DocumentShare> {
  return request<DocumentShare>(one(documentId), { method: 'DELETE' })
}

/** 分享页的地址。 */
export function shareUrl(token: string): string {
  return `${window.location.origin}/s/${encodeURIComponent(token)}`
}

/** Who shared the document, and the invite seats their own code still has. */
export interface SharedBy {
  handle: string
  nickname: string | null
  /** Their personal invite code; null when they have none to offer. */
  code: string | null
  seats_left: number
}

export interface SharedDocument {
  title: string | null
  content: string
  updated_at: string
  shared_by: SharedBy | null
}

const shared = (token: string) => `/shared/${encodeURIComponent(token)}`

/** 链接分享的那份文档的当前版本；不用登录。 */
export function readSharedDocument(token: string): Promise<SharedDocument> {
  return request<SharedDocument>(shared(token))
}

/** 分享的文档里引用的一张图：资料库里的才随文档分享。`<img>` 直接用这个地址。 */
export function sharedImageUrl(token: string, path: string): string {
  return `${BASE}${shared(token)}/image?path=${encodeURIComponent(path)}`
}

/** 分享页上的小程序从哪里取代码：只认这份文档自己的。 */
export function sharedDocApps(token: string): DocAppSource {
  return {
    app: (key) => request<DocApp>(`${shared(token)}/apps/${encodeURIComponent(key)}`),
    version: (key, version) =>
      request<DocAppVersion>(`${shared(token)}/apps/${encodeURIComponent(key)}/versions/${version}`),
    // 截图只在版本菜单里用，分享页没有那个菜单。
    shot: () => Promise.reject(new Error('no pictures on a shared page')),
  }
}
