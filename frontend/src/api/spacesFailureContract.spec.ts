// @vitest-environment jsdom
// The spaces domain's failure and edge evidence: what a refusal, an edge page,
// a lost network, a 401 and a blob failure each do to the new payload stack. The
// happy-path request/response contract for every method lives in
// `spacesContract.spec.ts`; this file is only the cases a success handler cannot
// reach, kept separate so neither spec becomes a bin.
import { delay, http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import {
  auth,
  contractEnvironment,
  envelope,
  newer,
  refresh,
  replace,
  server,
  uploadFile,
} from './requestContract.spec.helper'

// The reporter is a side effect (a queued fetch to the run records), not a
// method under test: silence it so a swallowed failure does not leave the
// suite with an unhandled request.
vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))

contractEnvironment()

let spaces: typeof import('./spaces').SpacesApi
let handler: typeof import('@/services/ErrorHandler').default
let newErrorMessage: (error: unknown, fallback: string) => string

beforeAll(async () => {
  ;({ SpacesApi: spaces } = await import('./spaces'))
  ;({ default: handler } = await import('@/services/ErrorHandler'))
  ;({ axiosErrorMessage: newErrorMessage } = await import('./axiosHttp'))
})

const structuredRefusal = (status: number) =>
  HttpResponse.json(
    {
      code: status,
      message: 'refused',
      data: null,
      error: { name: 'AccessDeniedError', message: 'You cannot read this', data: { reason: 'membership' } },
    },
    { status }
  )

describe('spaces: structured refusals keep their meaning', () => {
  it.each([400, 403, 404, 422, 500, 503])('%s keeps the condition name, sentence and data', async (status) => {
    server.use(http.get(`${newer}/spaces/7`, () => structuredRefusal(status)))
    const error = await spaces.detail(7).catch((failure) => failure)
    expect(error).toMatchObject({
      message: 'You cannot read this',
      status,
      code: 'AccessDeniedError',
      data: { reason: 'membership' },
    })
    expect(newErrorMessage(error, 'fallback')).toBe('You cannot read this')
  })

  it('keeps an unnamed 403 sentence rather than replacing it with a generic one', async () => {
    server.use(
      http.get(`${newer}/spaces/7`, () =>
        HttpResponse.json({ code: 403, message: 'Server sentence', data: null }, { status: 403 })
      )
    )
    const error = await spaces.detail(7).catch((failure) => failure)
    expect(newErrorMessage(error, 'fallback')).toBe('Server sentence')
  })

  it('reads an edge page as a transport failure, including a 200 one', async () => {
    for (const status of [200, 502]) {
      server.use(http.get(`${newer}/spaces/7`, () => new HttpResponse('<html>offline</html>', { status })))
      const error = (await spaces.detail(7).catch((failure) => failure)) as Error
      expect(error).toBeInstanceOf(Error)
      expect(newErrorMessage(error, 'fallback')).toBe(error.message)
    }
  })

  it('uses the caller fallback for a lost network without retrying', async () => {
    let requests = 0
    server.use(
      http.get(`${newer}/spaces/7`, () => {
        requests++
        return HttpResponse.error()
      })
    )
    const error = await spaces.detail(7).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
    expect(newErrorMessage(error, 'fallback')).toBe('fallback')
    expect(requests).toBe(1)
  })
})

describe('spaces: a read never invents a command success', () => {
  it.each([null, false, 0, ''])('a payload read returns falsy %j unchanged', async (value) => {
    server.use(http.get(`${newer}/spaces/7`, () => HttpResponse.json(envelope(value))))
    expect(await spaces.detail(7)).toEqual(value)
  })

  it('distinguishes a command sentinel from a caught failure for the caller', async () => {
    server.use(http.delete(`${newer}/spaces/7/members/9`, () => HttpResponse.json(envelope(null))))
    const success = vi.fn()
    const sentinel = await handler.withErrorHandling(() => spaces.removeMember(7, 9), { showToast: false })
    if (sentinel !== undefined) success()
    expect(sentinel).toBe(true)
    expect(success).toHaveBeenCalledOnce()

    server.use(
      http.delete(`${newer}/spaces/7/members/9`, () =>
        HttpResponse.json({ code: 500, message: 'boom', data: null }, { status: 500 })
      )
    )
    const failure = vi.fn()
    const caught = await handler.withErrorHandling(() => spaces.removeMember(7, 9), { showToast: false })
    if (caught !== undefined) failure()
    expect(caught).toBeUndefined()
    expect(failure).not.toHaveBeenCalled()
  })
})

describe('spaces: authentication edge cases', () => {
  const detailUnauthorized = () => {
    let requests = 0
    server.use(
      http.get(`${newer}/spaces/7`, ({ request }) => {
        requests++
        expect(request.url.startsWith(`${newer}/spaces/7`)).toBe(true)
        return new HttpResponse(null, { status: 401 })
      })
    )
    return () => requests
  }

  it('sends no bearer for a logged-out or empty-token caller', async () => {
    for (const state of [
      { loggedIn: false, accessToken: 'contract-token' },
      { loggedIn: true, accessToken: '' },
    ]) {
      auth.loggedIn = state.loggedIn
      auth.accessToken = state.accessToken
      server.use(
        http.get(`${newer}/spaces/7`, ({ request }) => {
          expect(request.headers.get('authorization')).toBeNull()
          return HttpResponse.json(envelope({ space: { id: 7 } }))
        })
      )
      expect(await spaces.detail(7)).toEqual({ space: { id: 7 } })
    }
  })

  it('refreshes once and retries with the returned token', async () => {
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    // One handler for the whole exchange: the first answer is the 401, the
    // second carries the payload and must arrive with the new token.
    const authorization: (string | null)[] = []
    server.use(
      http.get(`${newer}/spaces/7`, ({ request }) => {
        expect(request.url.startsWith(`${newer}/spaces/7`)).toBe(true)
        authorization.push(request.headers.get('authorization'))
        if (authorization.length === 1) return new HttpResponse(null, { status: 401 })
        return HttpResponse.json(envelope({ space: { id: 7 } }))
      })
    )
    expect(await spaces.detail(7)).toEqual({ space: { id: 7 } })
    expect(authorization).toEqual(['Bearer contract-token', 'Bearer refreshed-token'])
    expect(refresh).toHaveBeenCalledOnce()
  })

  // A renewal that fails — turned down or out of reach — is attempted once, and
  // that is all this layer owes. Ending the sign-in is no longer its job:
  // `lib/session.ts` announces a renewal the server turned down, and
  // `services/account.ts` drops the session and takes the person to a sign-in,
  // for both stacks (`account.sessionOver.spec.ts`).
  it('does not retry a failed renewal, and does not navigate to a sign-in itself', async () => {
    for (const outcome of [{ kind: 'rejected' as const }, { kind: 'unreachable' as const }]) {
      const count = detailUnauthorized()
      refresh.mockReset().mockResolvedValue(outcome)
      replace.mockReset()
      await expect(spaces.detail(7)).rejects.toBeInstanceOf(Error)
      expect(count()).toBe(1)
      expect(replace).not.toHaveBeenCalled()
    }
  })

  it('stops after a refreshed request is still unauthorized', async () => {
    const count = detailUnauthorized()
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    const error = await spaces.detail(7).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
    expect(count()).toBe(2)
    expect(refresh).toHaveBeenCalledOnce()
  })
})

describe('spaces: upload progress, timeout and multipart shape', () => {
  it('lets a slow upload finish and carries the 60s upload timeout, not the 30s default', async () => {
    const { axiosNewApi } = await import('./axiosHttp')
    // What is verified: the upload dial raises the per-request timeout above
    // the 30s instance default (read off the request as it reaches the
    // transport), a delayed upload still completes, and progress fires and
    // settles at loaded === total. What is NOT verified here: a real
    // 30s-against-60s wall-clock boundary. MSW's XHR shim ignores xhr.timeout
    // (a 50ms-timeout request answered after 600ms still resolves), and a
    // genuine boundary would need a real network stack or a 30s wait — neither
    // fits this tier. Treat that boundary as open.
    expect(axiosNewApi.defaults.timeout).toBe(30000)
    let seenTimeout: number | undefined
    const interceptor = axiosNewApi.interceptors.request.use((config) => {
      seenTimeout = config.timeout
      return config
    })
    try {
      const progress: number[] = []
      let final: { loaded: number; total: number } | undefined
      server.use(
        http.post(`${newer}/spaces/7/materials`, async () => {
          await delay(400)
          return HttpResponse.json(envelope({ material: { id: 1 } }), { status: 201 })
        })
      )
      const material = await spaces.uploadMaterial(7, uploadFile(), 'members', (event) => {
        progress.push(event.loaded ?? 0)
        final = { loaded: event.loaded ?? 0, total: event.total ?? 0 }
      })
      expect(material).toEqual({ material: { id: 1 } })
      // The event measures the request body (the multipart envelope around the
      // file, so a few bytes more than the file alone).
      expect(progress.length).toBeGreaterThan(0)
      expect(final!.total).toBeGreaterThan(uploadFile().size)
      expect(final!.loaded).toBe(final!.total)
      for (let i = 1; i < progress.length; i++) expect(progress[i]).toBeGreaterThanOrEqual(progress[i - 1])
      expect(seenTimeout).toBe(60000)
    } finally {
      axiosNewApi.interceptors.request.eject(interceptor)
    }
  })

  it('keeps multipart fields as an ordered entry list, not a folded object', async () => {
    const seen: unknown[] = []
    server.use(
      http.post(`${newer}/spaces/7/materials`, async ({ request }) => {
        expect(request.headers.get('content-type')).toContain('multipart/form-data; boundary=')
        const form = await request.formData()
        seen.push(
          await Promise.all(
            [...form.entries()].map(async ([key, value]) =>
              typeof value === 'string' ? [key, value] : [key, { name: value.name, type: value.type }]
            )
          )
        )
        return HttpResponse.json(envelope({ material: { id: 1 } }), { status: 201 })
      })
    )
    await spaces.uploadMaterial(7, uploadFile(), 'members')
    expect(seen[0]).toEqual([
      ['file', { name: 'notes.pdf', type: 'application/pdf' }],
      ['visibility', 'members'],
    ])
  })
})

describe('spaces: material downloads carry bytes, not sentences', () => {
  // Non-ASCII but valid UTF-8 (0, CR LF, é, €, PNG): a byte-for-byte read.
  const bytes = [0, 13, 10, 195, 169, 226, 130, 172, 80, 78, 71]

  it('preserves the MIME and the non-ASCII bytes of a download', async () => {
    server.use(
      http.get(
        `${newer}/spaces/7/materials/9/download`,
        () => new HttpResponse(new Uint8Array(bytes), { status: 200, headers: { 'Content-Type': 'application/pdf' } })
      )
    )
    const blob = await spaces.downloadMaterial(7, 9)
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual(bytes)
    expect(blob.type).toBe('application/pdf')
  })

  // Bytes that are not valid UTF-8: the XHR/jsdom harness decodes the response
  // body as text and replaces each invalid byte with U+FFFD (EF BF BD), so the
  // download still hands the caller a blob — never an error — carrying the
  // response's own MIME type and the decoded bytes.
  it('hands an invalid-UTF8 download back as a blob with the response type', async () => {
    const invalid = [0, 255, 137, 80, 78, 71, 226, 130, 172]
    server.use(
      http.get(
        `${newer}/spaces/7/materials/9/download`,
        () =>
          new HttpResponse(new Uint8Array(invalid), {
            status: 200,
            headers: { 'Content-Type': 'application/octet-stream' },
          })
      )
    )
    const blob = await spaces.downloadMaterial(7, 9)
    // 0xFF and 0x89 are not valid UTF-8 and surface as the replacement sequence.
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([
      0, 239, 191, 189, 239, 191, 189, 80, 78, 71, 226, 130, 172,
    ])
    expect(blob.type).toBe('application/octet-stream')
  })

  it('answers an empty material with an empty blob, not an error or a fake success', async () => {
    server.use(http.get(`${newer}/spaces/7/materials/9/download`, () => new HttpResponse(null, { status: 200 })))
    const blob = await spaces.downloadMaterial(7, 9)
    expect(blob).toBeInstanceOf(Blob)
    expect(blob.size).toBe(0)
  })

  it('rejects a refused or edge-page download instead of returning it as content', async () => {
    const refusals = [
      () =>
        HttpResponse.json(
          { code: 403, message: 'forbidden', data: null, error: { name: 'AccessDeniedError', message: 'forbidden' } },
          { status: 403 }
        ),
      () => new HttpResponse('<html>offline</html>', { status: 502, headers: { 'Content-Type': 'text/html' } }),
    ]
    for (const respond of refusals) {
      server.use(http.get(`${newer}/spaces/7/materials/9/download`, respond))
      const error = await spaces.downloadMaterial(7, 9).catch((failure) => failure)
      expect(error).toBeInstanceOf(Error)
      expect(error).not.toBeInstanceOf(Blob)
    }
  })
})
