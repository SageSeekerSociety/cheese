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
  // 304 交回的就是攥着的那一个对象：调用方 `tasks.value = payload.data` 是同引用赋值，
  // Vue 的 ref setter 跳过触发，这一轮不重画。
  const second = await listRoomTasks('r1', { limit: 0 })
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
  const second = await listRoomTasks('r1', { limit: 0 })
  expect(second).not.toBe(first)
  expect(second.data.map((t) => t.id)).toEqual(['k1', 'k2'])
})

it('the project task list is cached under its own path, not the room one', async () => {
  const fetcher = vi.fn((url: string) => Promise.resolve(json({ data: [{ id: url }], total: 1 }, { ETag: '"v1"' })))
  vi.stubGlobal('fetch', fetcher)
  const project = await listProjectTasks('p1')
  const room = await listRoomTasks('r1', { limit: 0 })
  expect(project.data[0].id).toContain('/projects/p1/tasks')
  expect(room.data[0].id).toContain('/topics/r1/tasks')
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('a second reader signs in mid-flight — the pending read is not shared across tokens', async () => {
  localStorage.setItem('accessToken', 'user-a')
  let resolveA!: (value: Response) => void
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(new Promise((yes) => (resolveA = yes)))
    .mockImplementation(() => Promise.resolve(json({ data: [], total: 0 })))
  vi.stubGlobal('fetch', fetcher)
  const first = listRoomTasks('r1', { limit: 0 })
  await Promise.resolve()
  localStorage.setItem('accessToken', 'user-b')
  const second = listRoomTasks('r1', { limit: 0 })
  await Promise.resolve()
  // 换了人就不能跟上一个还在飞的：键里的 token 不同，第二个人自己发一条。
  expect(fetcher).toHaveBeenCalledTimes(2)
  resolveA(json({ data: [], total: 0 }))
  await Promise.all([first, second])
})
