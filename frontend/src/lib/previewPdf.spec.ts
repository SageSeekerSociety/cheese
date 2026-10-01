import { afterEach, expect, it, vi } from 'vitest'

import { previewDocumentPdf, PreviewRendererUnavailable } from '../api'

import { createPreviewPdfReader } from './previewPdf'

afterEach(() => vi.unstubAllGlobals())

it('reads bytes and the source fingerprint from the same credentialed response', async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(new Uint8Array([1, 2, 3]), {
      headers: { 'X-Cheese-Source-Version': '0123456789abcdef' },
    })
  )
  vi.stubGlobal('fetch', fetcher)
  const read = createPreviewPdfReader('/api', () => ({ Authorization: 'Bearer test' }))
  const result = await read('room/a', 'deck x.pptx', 'task/a', 'committed')
  expect(Array.from(new Uint8Array(result.bytes))).toEqual([1, 2, 3])
  expect(result.sourceVersion).toBe('0123456789abcdef')
  expect(fetcher).toHaveBeenCalledWith(
    '/api/topics/room%2Fa/attachments/pdf?path=deck%20x.pptx&source=committed&task=task%2Fa',
    { headers: { Authorization: 'Bearer test' } }
  )
})

it.each([null, 'metadata-version', '0123456789abcde', '0123456789ABCDEF'])(
  'keeps reading but does not invent a source fingerprint for %s',
  async (version) => {
    const headers = new Headers()
    if (version !== null) headers.set('X-Cheese-Source-Version', version)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Uint8Array([7]), { headers })))
    const read = createPreviewPdfReader('/api', () => ({}))
    const result = await read('room', 'deck.pptx')
    expect(result.sourceVersion).toBeNull()
    expect(Array.from(new Uint8Array(result.bytes))).toEqual([7])
  }
)

it('preserves the old ArrayBuffer API even when the source header is absent', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Uint8Array([8, 9]))))
  const result = await previewDocumentPdf('room', 'deck.pptx')
  expect(result).toBeInstanceOf(ArrayBuffer)
  expect(Array.from(new Uint8Array(result))).toEqual([8, 9])
})

it('keeps the exported renderer-unavailable exception identity and error messages', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'renderer unavailable' }), { status: 503 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'cannot convert file' }), { status: 422 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(previewDocumentPdf('room', 'deck.pptx')).rejects.toBeInstanceOf(PreviewRendererUnavailable)
  await expect(previewDocumentPdf('room', 'deck.pptx')).rejects.toThrow('cannot convert file')
})
