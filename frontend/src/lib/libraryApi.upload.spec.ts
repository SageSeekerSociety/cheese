// Putting a file into the library: the bytes go to the address the server hands
// out, never through the backend's own upload body, and the file is registered
// with the token that came with that address.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { replaceLibraryFile, uploadLibraryFile } from './libraryApi'

interface Seen {
  method: string
  url: string
  body: unknown
}

function fakeServer(address: string) {
  const seen: Seen[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      seen.push({ method, url, body: init?.body })
      const json = (data: unknown) =>
        new Response(JSON.stringify({ code: 200, message: 'ok', data }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      if (url.endsWith('/library/uploads')) return json({ url: address, upload: 'sealed-token' })
      if (url === address || url.endsWith(address)) return new Response(null, { status: 200 })
      return json({ path: '合同/报价.xlsx', bytes: 5 })
    })
  )
  return seen
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('uploading a library file', () => {
  it('puts the bytes at the bucket address and registers them with its token', async () => {
    const seen = fakeServer('https://bucket.example/incoming/1?X-Amz-Signature=s')
    const file = new File(['hello'], '报价.xlsx')

    const placed = await uploadLibraryFile('p1', file, '合同')

    expect(placed.path).toBe('合同/报价.xlsx')
    const put = seen.find((s) => s.method === 'PUT')
    expect(put?.url).toBe('https://bucket.example/incoming/1?X-Amz-Signature=s')
    expect(put?.body).toBe(file)
    const registered = seen.at(-1)!
    expect(registered.method).toBe('POST')
    expect(registered.url).toMatch(/\/projects\/p1\/library$/)
    expect(JSON.parse(String(registered.body))).toEqual({
      upload: 'sealed-token',
      filename: '报价.xlsx',
      folder: '合同',
    })
  })

  it('sends the bytes to the backend address when files are kept on its disk', async () => {
    const seen = fakeServer('/files/incoming/tok')

    await uploadLibraryFile('p1', new File(['hello'], 'a.txt'))

    expect(seen.find((s) => s.method === 'PUT')?.url).toBe('/api/files/incoming/tok')
  })

  it('registers a replacement under the name it replaces', async () => {
    const seen = fakeServer('https://bucket.example/incoming/2')

    await replaceLibraryFile('p1', '预算表.xlsx', new File(['v2'], '预算表-新.xlsx'))

    const registered = seen.at(-1)!
    expect(registered.method).toBe('PUT')
    expect(decodeURIComponent(registered.url)).toMatch(/\/library\?path=预算表\.xlsx$/)
    expect(JSON.parse(String(registered.body))).toEqual({ upload: 'sealed-token' })
  })

  it('registers nothing when the bytes did not arrive', async () => {
    const seen = fakeServer('https://bucket.example/incoming/3')
    const fetchMock = vi.mocked(fetch)
    const answer = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation(async (input, init) =>
      String(input).startsWith('https://bucket.example') ? new Response(null, { status: 403 }) : answer(input, init)
    )

    await expect(uploadLibraryFile('p1', new File(['x'], 'a.txt'))).rejects.toThrow()

    expect(seen.filter((s) => s.method === 'POST' && s.url.endsWith('/library'))).toEqual([])
  })
})
