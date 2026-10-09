import { afterEach, expect, it, vi } from 'vitest'

import { listProjectTasks, listRoomTasks } from './api'

function json(data: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ code: 200, data }), {
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

it('an unchanged room task list is one request that hands back the same payload', async () => {
  const etag = '"v1"'
  const body = { data: [{ id: 'k1' }], total: 1 }
  const fetcher = vi.fn((_url: string, init: RequestInit) => {
    const sent = new Headers(init.headers).get('If-None-Match')
    if (sent === etag) return Promise.resolve(new Response(null, { status: 304, headers: { ETag: etag } }))
    return Promise.resolve(json(body, { ETag: etag }))
  })
  vi.stubGlobal('fetch', fetcher)
  const first = await listRoomTasks('r1', { limit: 0 })
  expect(first.data.map((t) => t.id)).toEqual(['k1'])
  // 304 交回的就是手上那一个对象：缓存里的数据没换引用，读它的地方这一轮不重画。
  const second = await listRoomTasks('r1', { limit: 0 }, first)
  expect(second).toBe(first)
  expect(fetcher).toHaveBeenCalledTimes(2)
  const [url, init] = fetcher.mock.calls[1]
  expect(url).toContain('/topics/r1/tasks?limit=0')
  expect(new Headers(init.headers).get('If-None-Match')).toBe(etag)
})

it('a changed room task list comes back as a fresh payload', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(json({ data: [{ id: 'k1' }], total: 1 }, { ETag: '"v1"' }))
    .mockResolvedValueOnce(json({ data: [{ id: 'k1' }, { id: 'k2' }], total: 2 }, { ETag: '"v2"' }))
  vi.stubGlobal('fetch', fetcher)
  const first = await listRoomTasks('r1', { limit: 0 })
  const second = await listRoomTasks('r1', { limit: 0 }, first)
  expect(second).not.toBe(first)
  expect(second.data.map((t) => t.id)).toEqual(['k1', 'k2'])
})

it('an unchanged project task list hands back the same payload', async () => {
  const etag = '"p1"'
  const fetcher = vi.fn((_url: string, init: RequestInit) => {
    if (new Headers(init.headers).get('If-None-Match') === etag) {
      return Promise.resolve(new Response(null, { status: 304, headers: { ETag: etag } }))
    }
    return Promise.resolve(json({ data: [{ id: 'k1' }], total: 1 }, { ETag: etag }))
  })
  vi.stubGlobal('fetch', fetcher)
  const first = await listProjectTasks('p1', { open: true })
  expect(await listProjectTasks('p1', { open: true }, first)).toBe(first)
  expect(fetcher.mock.calls[1][0]).toContain('/projects/p1/tasks?status=open')
})

it('a first read with nothing held asks without a version and takes the body', async () => {
  const fetcher = vi.fn(() => Promise.resolve(json({ data: [{ id: 'k1' }], total: 1 }, { ETag: '"v1"' })))
  vi.stubGlobal('fetch', fetcher)
  // 换了人之后缓存是空的：头一次读不带上一个人那份的版本号。
  await listRoomTasks('r1', { limit: 0 })
  await listRoomTasks('r1', { limit: 0 })
  for (const [, init] of fetcher.mock.calls as unknown as [string, RequestInit][]) {
    expect(new Headers(init.headers).get('If-None-Match')).toBeNull()
  }
})
