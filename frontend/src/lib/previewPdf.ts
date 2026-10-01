import type { FileSource } from '../cx_types'

export class PreviewRendererUnavailable extends Error {}

export interface PdfPreview {
  bytes: ArrayBuffer
  sourceVersion: string | null
}

export function createPreviewPdfReader(base: string, authHeaders: () => Record<string, string>) {
  return async function previewDocumentPdfSnapshot(
    topicId: string,
    path: string,
    task?: string | null,
    source: FileSource = 'live'
  ): Promise<PdfPreview> {
    const url =
      `${base}/topics/${encodeURIComponent(topicId)}/attachments/pdf` +
      `?path=${encodeURIComponent(path)}&source=${source}` +
      (task ? `&task=${encodeURIComponent(task)}` : '')
    const res = await fetch(url, { headers: authHeaders() })
    if (res.ok) {
      const version = res.headers.get('X-Cheese-Source-Version')
      const bytes = await res.arrayBuffer()
      return { bytes, sourceVersion: version && /^[0-9a-f]{16}$/.test(version) ? version : null }
    }
    let message = ''
    try {
      message = String((await res.json())?.message || '')
    } catch {
      message = ''
    }
    if (res.status === 503) {
      throw new PreviewRendererUnavailable(message || '这个部署没有启用文档预览')
    }
    throw new Error(message || `无法生成预览（HTTP ${res.status}）`)
  }
}
