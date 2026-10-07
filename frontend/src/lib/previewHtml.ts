// 一份文档的「网页视图」：一个自包含的 HTML 页面，元素上带着 OfficeCLI 认的地址。
//
// 和 previewPdf.ts 是一对，不是替代：那一边取的是印出来的样子，这一边取的是还能被
// 指认的样子——第 7 段是 `/body/p[7]`，B2 那一格是 `/数据/B2`，跟改它的人用的字符串
// 是同一个。公式和 3D 模型会降级，理由写在后端那条路由上。
//
// 和 PDF 一样从 Authorization 头认人，所以也只能 fetch 回来再交给查看器：挂成
// iframe 的 src 是匿名请求，会拿到 401 白框。
import type { FileSource } from '../cx_types'

import { authHeaders, BASE } from '../api/http'

import { previewRefusal, sourceVersionOf } from './previewPdf'

export interface PagePreview {
  html: string
  sourceVersion: string | null
}

export function createPreviewHtmlReader(base: string, authHeaders: () => Record<string, string>) {
  return async function previewDocumentPageSnapshot(
    topicId: string,
    path: string,
    task?: string | null,
    source: FileSource = 'live'
  ): Promise<PagePreview> {
    const url =
      `${base}/topics/${encodeURIComponent(topicId)}/attachments/html` +
      `?path=${encodeURIComponent(path)}&source=${source}` +
      (task ? `&task=${encodeURIComponent(task)}` : '')
    const res = await fetch(url, { headers: authHeaders() })
    if (res.ok) {
      const html = await res.text()
      return { html, sourceVersion: sourceVersionOf(res) }
    }
    return await previewRefusal(res)
  }
}

/** 带上应用自己的凭据和地址的那一只。挂在这里而不是 `api.ts`：那一份已经超了仓库
 *  给自己的长度上限，只能变短。 */
export const previewDocumentPageSnapshot = createPreviewHtmlReader(BASE, authHeaders)
