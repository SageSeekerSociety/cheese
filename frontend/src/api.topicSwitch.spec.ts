import { afterEach, expect, it, vi } from 'vitest'

import { listTopics } from './api'

function json(data: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ code: 200, data }), {
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}
afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
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
  // very same object the cache holds, so nothing that reads it re-renders.
  const second = await listTopics('p', undefined, first)
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
  const second = await listTopics('p', undefined, first)
  expect(second).not.toBe(first)
  expect(second.data.map((t) => t.id)).toEqual(['t1', 't2'])
  const [, init] = fetcher.mock.calls[1]
  expect(new Headers(init.headers).get('If-None-Match')).toBe(etag1)
})
