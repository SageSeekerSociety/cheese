import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { usePendingAttachments } from './attachments'

function file(name: string, type: string): File {
  return new File(['x'], name, { type })
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url, options) => {
      const uploaded = options.body.get('file') as File
      return {
        ok: true,
        status: 200,
        json: async () => ({
          code: 200,
          data: { path: `uploads/id/${uploaded.name}`, mime: uploaded.type },
        }),
      }
    })
  )
})
afterEach(() => vi.unstubAllGlobals())

describe('chat attachments', () => {
  it('does not attach an upload to a different topic after navigation', async () => {
    let topic = 'first'
    let finish!: (value: unknown) => void
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            finish = resolve
          })
      )
    )
    const { addFiles, pending } = usePendingAttachments(() => topic)
    const uploading = addFiles([file('paper.pdf', 'application/pdf')])
    topic = 'second'
    finish({
      ok: true,
      json: async () => ({ code: 200, data: { path: 'uploads/paper.pdf', mime: 'application/pdf' } }),
    })
    await uploading
    expect(pending.value).toHaveLength(0)
  })

  it('uploads a PDF with its filename', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)

    await addFiles([file('paper.pdf', 'application/pdf')])

    expect(onError).not.toHaveBeenCalled()
    expect(pending.value).toEqual([{ path: 'uploads/id/paper.pdf', mime: 'application/pdf' }])
  })

  it('uploads images and documents together', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)

    await addFiles([file('a.png', 'image/png'), file('b.csv', 'text/csv')])

    expect(onError).not.toHaveBeenCalled()
    expect(pending.value.map((a) => a.path)).toEqual(['uploads/id/a.png', 'uploads/id/b.csv'])
  })

  it('does not upload without a topic', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => null, onError)
    await addFiles([file('paper.pdf', 'application/pdf')])
    expect(onError).not.toHaveBeenCalled()
    expect(pending.value).toHaveLength(0)
  })

  it('reports the count limit', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)
    await addFiles(Array.from({ length: 10 }, (_, i) => file(`${i}.txt`, 'text/plain')))
    expect(pending.value).toHaveLength(9)
    expect(onError).toHaveBeenCalledWith('每条消息最多添加 9 个附件')
  })

  it('reports an oversized file and uploads the next one', async () => {
    const onError = vi.fn()
    const large = file('large.pdf', 'application/pdf')
    Object.defineProperty(large, 'size', { value: 10 * 1024 * 1024 + 1 })
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)
    await addFiles([large, file('small.pdf', 'application/pdf')])
    expect(onError).toHaveBeenCalledWith('large.pdf 超过 10MB，无法上传')
    expect(pending.value).toHaveLength(1)
    expect(pending.value[0].path).toContain('small.pdf')
  })
})
