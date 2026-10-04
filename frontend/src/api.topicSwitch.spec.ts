import { afterEach, expect, it, vi } from 'vitest'

import { getDocVersions } from './api/docHistory'
import { getDocNodes, listBlocks, listTopics } from './api'

function json(data: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ code: 200, data }), {
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}
afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

it('opening a topic reads the doc node tree once for the badge and the anchor', async () => {
  let resolve!: (value: Response) => void
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(new Promise((yes) => (resolve = yes)))
    .mockImplementation(() => Promise.resolve(json({ data: [], total: 0 })))
  vi.stubGlobal('fetch', fetcher)
  // usePanelDoc.loadNodes and DocSurface.fetchDocNodes race on mount.
  const reads = [getDocNodes('room'), getDocNodes('room')]
  await Promise.resolve()
  expect(fetcher).toHaveBeenCalledTimes(1)
  resolve(json({ data: [{ id: 'b1' }], total: 1 }))
  expect((await Promise.all(reads)).map((r) => r.data[0].id)).toEqual(['b1', 'b1'])
  // A later explicit refresh (after a write) must still hit the server.
  await getDocNodes('room')
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('opening a topic reads the doc history once for "latest edit" and the changes panel', async () => {
  let resolve!: (value: Response) => void
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(new Promise((yes) => (resolve = yes)))
    .mockImplementation(() => Promise.resolve(json({ versions: [], total: 0 })))
  vi.stubGlobal('fetch', fetcher)
  const reads = [getDocVersions('room', { limit: 1 }), getDocVersions('room', { limit: 1 })]
  await Promise.resolve()
  expect(fetcher).toHaveBeenCalledTimes(1)
  resolve(json({ versions: [], total: 0 }))
  await Promise.all(reads)
  await getDocVersions('room', { limit: 1 })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('a topic switch fetches the newest block page once for the prefetch and the panel', async () => {
  let resolve!: (value: Response) => void
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(new Promise((yes) => (resolve = yes)))
    .mockImplementation(() => Promise.resolve(json({ data: [], has_more: false })))
  vi.stubGlobal('fetch', fetcher)
  // lib/blockCache.refreshBlockCache and useChatPanel both ask for the first page.
  const reads = [listBlocks('room', { limit: 30 }), listBlocks('room', { limit: 30 })]
  await Promise.resolve()
  expect(fetcher).toHaveBeenCalledTimes(1)
  resolve(json({ data: [{ id: 'm1' }], has_more: false }))
  expect((await Promise.all(reads)).map((r) => r.data[0].id)).toEqual(['m1', 'm1'])
})

it('cursor pages are never shared — each is a distinct window', async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(json({ data: [], has_more: false })))
  vi.stubGlobal('fetch', fetcher)
  await Promise.all([
    listBlocks('room', { limit: 30, before: 'm5' }),
    listBlocks('room', { limit: 30, before: 'm5' }),
    listBlocks('room', { limit: 30, around: 'm3' }),
  ])
  expect(fetcher).toHaveBeenCalledTimes(3)
})

it('an unchanged topic-list poll costs one request and reuses the same payload', async () => {
  const etag = '"v1"'
  const body = { data: [{ id: 't1' }], total: 1 }
  const fetcher = vi.fn((_url: string, init: RequestInit) => {
    const sent = new Headers(init.headers).get('If-None-Match')
    if (sent === etag) return Promise.resolve(new Response(null, { status: 304, headers: { ETag: etag } }))
    return Promise.resolve(json(body, { ETag: etag }))
  })
  vi.stubGlobal('fetch', fetcher)
  const first = await listTopics('p')
  expect(first.data[0].id).toBe('t1')
  // The poll sends If-None-Match; the server answers 304 and we hand back the
  // very same object, so `topics.value = payload.data` is a no-op assignment.
  const second = await listTopics('p')
  expect(second).toBe(first)
  expect(fetcher).toHaveBeenCalledTimes(2)
  const [, init] = fetcher.mock.calls[1]
  expect(new Headers(init.headers).get('If-None-Match')).toBe(etag)
})

it('a changed topic list comes back as a fresh payload', async () => {
  const etag1 = '"v1"'
  const etag2 = '"v2"'
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(json({ data: [{ id: 't1' }], total: 1 }, { ETag: etag1 }))
    .mockResolvedValueOnce(json({ data: [{ id: 't1' }, { id: 't2' }], total: 2 }, { ETag: etag2 }))
  vi.stubGlobal('fetch', fetcher)
  const first = await listTopics('p')
  const second = await listTopics('p')
  expect(second).not.toBe(first)
  expect(second.data.map((t) => t.id)).toEqual(['t1', 't2'])
  const [, init] = fetcher.mock.calls[1]
  expect(new Headers(init.headers).get('If-None-Match')).toBe(etag1)
})
