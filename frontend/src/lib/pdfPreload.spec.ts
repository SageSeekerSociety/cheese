import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { preloadPdfViewer, resetPdfPreload } from './pdfPreload'

const loaded = vi.hoisted(() => ({ lib: 0 }))
vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => {
  loaded.lib += 1
  return { version: 'x' }
})
vi.mock('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url', () => ({ default: '/assets/pdf.worker.mjs' }))

describe('进房间后预取 pdf.js', () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response('worker'))

  beforeEach(() => {
    vi.useFakeTimers()
    resetPdfPreload()
    fetchMock.mockClear()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('空闲时取一次主体和 worker，重复调用不重复取', async () => {
    preloadPdfViewer()
    preloadPdfViewer()
    expect(fetchMock).not.toHaveBeenCalled()

    await vi.runAllTimersAsync()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0][0]).toBe('/assets/pdf.worker.mjs')
    expect(loaded.lib).toBe(1)

    preloadPdfViewer()
    await vi.runAllTimersAsync()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
