// @vitest-environment jsdom
// The real wall-clock boundary the browser tier cannot show. MSW's XHR shim
// ignores `xhr.timeout` (a 50ms-timeout request answered after 600ms still
// resolves), so the 30s-default / 60s-upload dial can only be observed over a
// real socket with the node `http` adapter — see `taskBoardFailureContract.spec`
// for the part that is checkable in the browser tier (the dial reads 60000 while
// the instance default is 30000).
//
// This spec talks to a real local HTTP server that holds the response open past
// the 30s default, and needs ~65s of wall clock, so it only runs when
// REAL_UPLOAD_BOUNDARY is set. The default tier skips it.
import { File } from 'node:buffer'
import { createServer, type Server } from 'node:http'

import type { Socket } from 'node:net'

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'

const RUN = process.env.REAL_UPLOAD_BOUNDARY === '1'
// The server holds the response open for this long: above the 30s instance
// default and below the 60s per-upload dial, so the two arms differ.
const DELAY_MS = 35000

// The app's error reporter is a side effect (a queued fetch to the run records),
// and the auth/session/router/toast singletons pull DOM-only modules — none are
// under test here, they only have to load so `axiosHttp` can build its instance.
vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, accessToken: '' } }))
vi.mock('@/lib/session', () => ({ refreshSession: vi.fn() }))
vi.mock('@/router', () => ({ default: { replace: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

const envelope = (data: unknown, code = 200) => JSON.stringify({ code, message: 'ok', data })

describe.skipIf(!RUN)('task board upload: real 30s / 60s wall-clock boundary', () => {
  let server: Server
  let origin: string
  let axiosNewApi: typeof import('./axiosHttp').axiosNewApi
  let TaskBoardApi: typeof import('./taskBoard').TaskBoardApi
  const timers: NodeJS.Timeout[] = []
  const sockets = new Set<Socket>()

  beforeAll(async () => {
    server = createServer((req, res) => {
      req.on('error', () => undefined)
      res.on('error', () => undefined)
      // Drain the request body, then hold the answer until after the 30s mark.
      req.resume()
      req.on('end', () => {
        const timer = setTimeout(() => {
          try {
            res.writeHead(201, { 'content-type': 'application/json' })
            res.end(envelope({ attachment: { id: 1 } }))
          } catch {
            /* the client may have aborted the socket at the 30s boundary */
          }
        }, DELAY_MS)
        timers.push(timer)
      })
    })
    server.on('connection', (socket) => {
      sockets.add(socket)
      socket.on('close', () => sockets.delete(socket))
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    origin = `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`

    // Stub before importing: `axiosHttp` reads `import.meta.env` once, at import.
    vi.stubEnv('VITE_API_BASE_URL', origin)
    vi.stubEnv('VITE_NEW_API_BASE_URL', origin)
    ;({ axiosNewApi } = await import('./axiosHttp'))
    ;({ TaskBoardApi } = await import('./taskBoard'))
    // In the node environment there is no XHR, but pin the adapter so the socket
    // timeout — not the MSW XHR shim — is what is measured.
    axiosNewApi.defaults.adapter = 'http'
  }, 120000)

  afterAll(async () => {
    timers.forEach((timer) => clearTimeout(timer))
    sockets.forEach((socket) => socket.destroy())
    await new Promise<void>((resolve) => server.close(() => resolve()))
    vi.unstubAllEnvs()
  })

  it('lets a 35s response through on the 60s upload dial', async () => {
    const started = Date.now()
    const result = await TaskBoardApi.uploadAttachment(
      7,
      new File(['%PDF-1.7'], 'notes.pdf', { type: 'application/pdf' })
    )
    const elapsed = Date.now() - started
    expect(result).toEqual({ attachment: { id: 1 } })
    expect(elapsed).toBeGreaterThanOrEqual(DELAY_MS)
  }, 90000)

  it('aborts the same 35s response on the 30s default dial, before the server answers', async () => {
    const started = Date.now()
    const error = (await TaskBoardApi.listAttachments(7).catch((failure) => failure)) as { message?: string }
    const elapsed = Date.now() - started
    expect(error).toBeInstanceOf(Error)
    // `axiosHttp` maps a transport timeout to a plain Error carrying axios's own
    // sentence (no `.code`): the dial that fired is the 30s instance default.
    expect(error.message).toMatch(/timeout of 30000ms/)
    // Aborted at the 30s dial: comfortably before the 35s answer, and never
    // instant — a lost-network failure would return at once.
    expect(elapsed).toBeGreaterThanOrEqual(29000)
    expect(elapsed).toBeLessThan(DELAY_MS)
  }, 90000)
})
