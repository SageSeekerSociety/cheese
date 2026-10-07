import type { FileSource } from '../cx_types'

import { t } from '../i18n'

export class PreviewRendererUnavailable extends Error {}

export interface PdfPreview {
  bytes: ArrayBuffer
  sourceVersion: string | null
}

/** 转换服务拒绝时说的那句话。503 是「这个部署没有转换服务」——换个文件也一样，
 *  所以它有自己的类型；其余是「这份文件转不了」，读者重来一次也不会变。 */
export async function previewRefusal(res: Response): Promise<never> {
  let message = ''
  try {
    message = String((await res.json())?.message || '')
  } catch {
    message = ''
  }
  if (res.status === 503) {
    throw new PreviewRendererUnavailable(message || t('apiError.previewDisabled'))
  }
  throw new Error(message || t('files.preview.generateFailed', { status: res.status }))
}

/** 响应头里那个源版本：十六进制 16 位才算数，别的一律当没有。 */
export function sourceVersionOf(res: Response): string | null {
  const version = res.headers.get('X-Cheese-Source-Version')
  return version && /^[0-9a-f]{16}$/.test(version) ? version : null
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
      const bytes = await res.arrayBuffer()
      return { bytes, sourceVersion: sourceVersionOf(res) }
    }
    return await previewRefusal(res)
  }
}
