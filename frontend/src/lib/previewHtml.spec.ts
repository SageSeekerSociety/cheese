import { afterEach, expect, it, vi } from 'vitest'

import { createPreviewHtmlReader, previewDocumentPageSnapshot } from './previewHtml'
import { PreviewRendererUnavailable } from './previewPdf'

afterEach(() => vi.unstubAllGlobals())

it('reads a whole page of text and the source fingerprint from the same credentialed response', async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response('<html><head></head><body>hi</body></html>', {
      headers: { 'X-Cheese-Source-Version': '0123456789abcdef' },
    })
  )
  vi.stubGlobal('fetch', fetcher)
  const read = createPreviewHtmlReader('/api', () => ({ Authorization: 'Bearer test' }))
  const result = await read('room/a', 'sheet x.xlsx', 'task/a', 'committed')
  expect(result.html).toBe('<html><head></head><body>hi</body></html>')
  expect(result.sourceVersion).toBe('0123456789abcdef')
  // 换成 /web 之类的新路径会静默地什么都取不到：这条路由的名字要和后端那一条对上。
  expect(fetcher).toHaveBeenCalledWith(
    '/api/topics/room%2Fa/attachments/html?path=sheet%20x.xlsx&source=committed&task=task%2Fa',
    { headers: { Authorization: 'Bearer test' } }
  )
})

it('asks for the room file when no task is named', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('<html></html>'))
  vi.stubGlobal('fetch', fetcher)
  const read = createPreviewHtmlReader('/api', () => ({}))
  await read('room', 'report.docx')
  expect(fetcher).toHaveBeenCalledWith('/api/topics/room/attachments/html?path=report.docx&source=live', {
    headers: {},
  })
})

it('keeps reading but does not invent a source fingerprint', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html></html>')))
  const read = createPreviewHtmlReader('/api', () => ({}))
  expect((await read('room', 'report.docx')).sourceVersion).toBeNull()
})

it('tells a missing renderer apart from a file it cannot read', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'renderer unavailable' }), { status: 503 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'cannot convert file' }), { status: 422 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(previewDocumentPageSnapshot('room', 'report.docx')).rejects.toBeInstanceOf(PreviewRendererUnavailable)
  await expect(previewDocumentPageSnapshot('room', 'report.docx')).rejects.toThrow('cannot convert file')
})
