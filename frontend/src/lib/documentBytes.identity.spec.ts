import type { FileSource } from '../cx_types'
import type { PdfPreview } from './previewPdf'

import { defineComponent, h, nextTick, reactive } from 'vue'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { previewDocumentPdfSnapshot, previewFileBytes } from '../api'

import { useDocumentBytes } from './documentBytes'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, previewDocumentPdfSnapshot: vi.fn(), previewFileBytes: vi.fn() }
})
beforeEach(() => vi.resetAllMocks())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}

function mountSource(path = 'deck.pptx') {
  const source = reactive<{
    topicId: string
    path: string
    taskId: string | null
    source: FileSource
    version: string
    enabled: boolean
  }>({ topicId: 'room', path, taskId: 'task', source: 'committed', version: 'aaaaaaaaaaaaaaaa', enabled: true })
  let state!: ReturnType<typeof useDocumentBytes>
  const ui = render(
    defineComponent({
      setup() {
        state = useDocumentBytes({
          topicId: () => source.topicId,
          path: () => source.path,
          task: () => source.taskId,
          source: () => source.source,
          version: () => source.version,
          enabled: () => source.enabled,
        })
        return () => h('div')
      },
    })
  )
  return { ui, state, source }
}

it('publishes an atomic snapshot and keeps both old bytes and old identity on refresh failure', async () => {
  const original = new ArrayBuffer(8)
  vi.mocked(previewDocumentPdfSnapshot).mockResolvedValueOnce({ bytes: original, sourceVersion: 'aaaaaaaaaaaaaaaa' })
  const { state, source } = mountSource()
  await waitFor(() => expect(state.snapshot.value?.bytes).toBe(original))
  const displayed = state.snapshot.value
  vi.mocked(previewDocumentPdfSnapshot).mockRejectedValueOnce(new Error('conversion failed'))
  source.version = 'bbbbbbbbbbbbbbbb'
  await waitFor(() => expect(state.error.value).toBe('conversion failed'))
  expect(state.bytes.value).toBe(original)
  expect(state.snapshot.value).toBe(displayed)
  expect(state.snapshot.value?.identity.version).toBe('aaaaaaaaaaaaaaaa')
  expect(state.snapshot.value?.sourceVersion).toBe('aaaaaaaaaaaaaaaa')
})

it('retires a pending old conversion synchronously on version change', async () => {
  const old = deferred<PdfPreview>()
  const fresh = deferred<PdfPreview>()
  vi.mocked(previewDocumentPdfSnapshot).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const { state, source } = mountSource()
  source.version = 'bbbbbbbbbbbbbbbb'
  expect(previewDocumentPdfSnapshot).toHaveBeenCalledTimes(2)
  const bytes = new ArrayBuffer(16)
  fresh.resolve({ bytes, sourceVersion: source.version })
  await waitFor(() => expect(state.bytes.value).toBe(bytes))
  old.resolve({ bytes: new ArrayBuffer(8), sourceVersion: 'aaaaaaaaaaaaaaaa' })
  await nextTick()
  expect(state.snapshot.value?.identity.version).toBe('bbbbbbbbbbbbbbbb')
  expect(state.bytes.value).toBe(bytes)
  expect(previewDocumentPdfSnapshot).toHaveBeenLastCalledWith('room', 'deck.pptx', 'task', 'committed')
})

it('retires pending reads when disabled, clears on source switch, and rejects late completion after unmount', async () => {
  const old = deferred<PdfPreview>()
  const next = deferred<PdfPreview>()
  vi.mocked(previewDocumentPdfSnapshot).mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
  const { ui, state, source } = mountSource()
  source.enabled = false
  old.resolve({ bytes: new ArrayBuffer(8), sourceVersion: source.version })
  await nextTick()
  expect(state.snapshot.value).toBeNull()
  expect(state.loading.value).toBe(false)
  source.path = 'another.pptx'
  source.enabled = true
  expect(previewDocumentPdfSnapshot).toHaveBeenLastCalledWith('room', 'another.pptx', 'task', 'committed')
  ui.unmount()
  next.resolve({ bytes: new ArrayBuffer(16), sourceVersion: source.version })
  await nextTick()
  expect(state.snapshot.value).toBeNull()
})

it('checks the source again on completion even without a reactive watcher notification', async () => {
  const pending = deferred<PdfPreview>()
  vi.mocked(previewDocumentPdfSnapshot).mockReturnValueOnce(pending.promise)
  let version = 'aaaaaaaaaaaaaaaa'
  let state!: ReturnType<typeof useDocumentBytes>
  render(
    defineComponent({
      setup() {
        state = useDocumentBytes({ topicId: () => 'room', path: () => 'deck.pptx', version: () => version })
        return () => h('div')
      },
    })
  )
  version = 'bbbbbbbbbbbbbbbb'
  pending.resolve({ bytes: new ArrayBuffer(8), sourceVersion: 'aaaaaaaaaaaaaaaa' })
  await nextTick()
  expect(state.snapshot.value).toBeNull()
})

it('digests the actual original PDF bytes and retires a late digest', async () => {
  const original = new ArrayBuffer(8)
  const replacement = new ArrayBuffer(16)
  const oldDigest = deferred<ArrayBuffer>()
  const digest = vi
    .fn()
    .mockReturnValueOnce(oldDigest.promise)
    .mockResolvedValueOnce(new Uint8Array(32).fill(0xbb).buffer)
  vi.stubGlobal('crypto', { subtle: { digest } })
  vi.mocked(previewFileBytes).mockResolvedValueOnce(original).mockResolvedValueOnce(replacement)
  const { state, source } = mountSource('deck.pdf')
  await waitFor(() => expect(digest).toHaveBeenCalledWith('SHA-256', original))
  source.version = 'bbbbbbbbbbbbbbbb'
  await waitFor(() => expect(state.bytes.value).toBe(replacement))
  expect(digest).toHaveBeenLastCalledWith('SHA-256', replacement)
  expect(state.snapshot.value?.sourceVersion).toBe('bbbbbbbbbbbbbbbb')
  oldDigest.resolve(new Uint8Array(32).fill(0xaa).buffer)
  await nextTick()
  expect(state.bytes.value).toBe(replacement)
  expect(state.snapshot.value?.sourceVersion).toBe('bbbbbbbbbbbbbbbb')
  expect(previewDocumentPdfSnapshot).not.toHaveBeenCalled()
})

it('clears a displayed snapshot synchronously when the source path changes', async () => {
  vi.mocked(previewDocumentPdfSnapshot).mockResolvedValueOnce({
    bytes: new ArrayBuffer(8),
    sourceVersion: 'aaaaaaaaaaaaaaaa',
  })
  const next = deferred<PdfPreview>()
  const { state, source } = mountSource()
  await waitFor(() => expect(state.snapshot.value).not.toBeNull())
  vi.mocked(previewDocumentPdfSnapshot).mockReturnValueOnce(next.promise)
  source.path = 'next.pptx'
  expect(state.bytes.value).toBeNull()
  expect(state.snapshot.value).toBeNull()
})
