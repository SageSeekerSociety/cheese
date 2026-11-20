// Putting a file into the library: the bytes go, part by part, to the addresses
// the server hands out, never through the backend's own upload body, and the
// file is registered with the token that came with the upload, the SHA-256 of
// the whole file and the MD5 of each part, so the server never reads it back.
import { createHash } from 'node:crypto'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { replaceLibraryFile, uploadLibraryFile } from './libraryApi'

interface Seen {
  method: string
  url: string
  body: unknown
  headers: Record<string, string>
}

const sha = (data: string | Uint8Array) => createHash('sha256').update(data).digest('hex')
const md5 = (data: string | Uint8Array) => createHash('md5').update(data).digest('hex')

/** A server that cuts uploads into parts of `partBytes` and hands each part an
 *  address under `address` (`<address>/<part>`). */
function fakeServer(address: string, partBytes = 1024) {
  const seen: Seen[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      seen.push({ method, url, body: init?.body, headers: { ...(init?.headers as Record<string, string>) } })
      const json = (data: unknown) =>
        new Response(JSON.stringify({ code: 200, message: 'ok', data }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      if (url.endsWith('/library/uploads')) {
        const { size } = JSON.parse(String(init?.body))
        return json({ upload: 'sealed-token', part_bytes: partBytes, parts: Math.ceil(size / partBytes) })
      }
      if (url.endsWith('/library/uploads/part')) {
        const { part, md5: digest } = JSON.parse(String(init?.body))
        return json({ url: `${address}/${part}`, headers: { 'Content-MD5': digest } })
      }
      if (url.includes(address)) return new Response(null, { status: 200 })
      return json({ path: '合同/报价.xlsx', bytes: 5 })
    })
  )
  return seen
}

const puts = (seen: Seen[]) => seen.filter((s) => s.method === 'PUT' && !s.url.includes('/library?'))
const text = (body: unknown) => new TextDecoder().decode(body as Uint8Array)

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('uploading a library file', () => {
  it('puts the bytes at the bucket address and registers them with the digest', async () => {
    const seen = fakeServer('https://bucket.example/up')
    const file = new File(['hello'], '报价.xlsx')

    const placed = await uploadLibraryFile('p1', file, '合同')

    expect(placed.path).toBe('合同/报价.xlsx')
    const [put] = puts(seen)
    expect(put.url).toBe('https://bucket.example/up/1')
    expect(text(put.body)).toBe('hello')
    expect(put.headers).toEqual({ 'Content-MD5': md5('hello') })
    const registered = seen.at(-1)!
    expect(registered.method).toBe('POST')
    expect(registered.url).toMatch(/\/projects\/p1\/library$/)
    expect(JSON.parse(String(registered.body))).toEqual({
      upload: 'sealed-token',
      sha256: sha('hello'),
      parts: [md5('hello')],
      filename: '报价.xlsx',
      folder: '合同',
    })
  })

  it('sends a large file in parts that join back into the file', async () => {
    const seen = fakeServer('https://bucket.example/big', 4)
    const content = 'abcdefghij'

    await uploadLibraryFile('p1', new File([content], 'data.bin'))

    const sent = puts(seen).sort((a, b) => a.url.localeCompare(b.url))
    expect(sent.map((s) => s.url)).toEqual([
      'https://bucket.example/big/1',
      'https://bucket.example/big/2',
      'https://bucket.example/big/3',
    ])
    expect(sent.map((s) => text(s.body)).join('')).toBe(content)
    const body = JSON.parse(String(seen.at(-1)!.body))
    expect(body.sha256).toBe(sha(content))
    expect(body.parts).toEqual(['abcd', 'efgh', 'ij'].map((piece) => md5(piece)))
  })

  it('reports how much of a large file has landed, part by part, up to the whole', async () => {
    fakeServer('https://bucket.example/big', 4)
    const reported: number[] = []

    await uploadLibraryFile('p1', new File(['abcdefghij'], 'data.bin'), undefined, (sent) => reported.push(sent))

    expect(reported[0]).toBe(0)
    expect(reported.at(-1)).toBe(10)
    expect([...reported].sort((a, b) => a - b)).toEqual(reported)
    expect(reported.length).toBe(4)
  })

  it('sends the bytes to the backend address when files are kept on its disk', async () => {
    const seen = fakeServer('/files/incoming/tok')

    await uploadLibraryFile('p1', new File(['hello'], 'a.txt'))

    expect(puts(seen)[0].url).toBe('/api/files/incoming/tok/1')
  })

  it('registers a replacement under the name it replaces', async () => {
    const seen = fakeServer('https://bucket.example/incoming/2')

    await replaceLibraryFile('p1', '预算表.xlsx', new File(['v2'], '预算表-新.xlsx'))

    const registered = seen.at(-1)!
    expect(registered.method).toBe('PUT')
    expect(decodeURIComponent(registered.url)).toMatch(/\/library\?path=预算表\.xlsx$/)
    expect(JSON.parse(String(registered.body))).toEqual({
      upload: 'sealed-token',
      sha256: sha('v2'),
      parts: [md5('v2')],
    })
  })

  it('registers nothing when a part is refused', async () => {
    const seen = fakeServer('https://bucket.example/incoming/3', 1)
    const fetchMock = vi.mocked(fetch)
    const answer = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation(async (input, init) =>
      String(input) === 'https://bucket.example/incoming/3/2'
        ? new Response(null, { status: 403 })
        : answer(input, init)
    )

    await expect(uploadLibraryFile('p1', new File(['xyz'], 'a.txt'))).rejects.toThrow()

    expect(seen.filter((s) => s.method === 'POST' && s.url.endsWith('/library'))).toEqual([])
  })

  it('sends a part again when the connection drops on the way', async () => {
    const seen = fakeServer('https://bucket.example/flaky')
    const fetchMock = vi.mocked(fetch)
    const answer = fetchMock.getMockImplementation()!
    let dropped = false
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).startsWith('https://bucket.example/flaky') && !dropped) {
        dropped = true
        throw new TypeError('Failed to fetch')
      }
      return answer(input, init)
    })

    const placed = await uploadLibraryFile('p1', new File(['hello'], 'a.txt'))

    expect(placed.path).toBe('合同/报价.xlsx')
    expect(seen.filter((s) => s.method === 'POST' && s.url.endsWith('/library'))).toHaveLength(1)
  })
})
