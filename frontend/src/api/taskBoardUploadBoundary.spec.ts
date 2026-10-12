// @vitest-environment jsdom
// The real wall-clock boundary the browser tier cannot show. MSW's XHR shim
// ignores `xhr.timeout` (a 50ms-timeout request answered after 600ms still
// resolves), so the 30s-default / 60s-upload dial can only be observed over a
// real socket with the node `http` adapter — see `taskBoardFailureContract.spec`
// for the browser-tier part (the upload dial reads 60000 while the instance
// default is 30000).
//
// This spec talks to a real local HTTP server that holds the response open past
// the dial, and needs ~3.5 min of wall clock, so it only runs when
// REAL_UPLOAD_BOUNDARY is set. The default tier skips it.
//
// Both stacks are exercised, old then new, so the dial parity is shown, not
// asserted from source:
//   45s answer → both uploads resolve (the 60s upload dial holds).
//   65s answer → the old and new 30s default dials abort before it (~30s), and
//                the new 60s upload dial aborts at ~60s.
import { File } from 'node:buffer'
import { createServer, type Server } from 'node:http'

import type { Socket } from 'node:net'

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'

const RUN = process.env.REAL_UPLOAD_BOUNDARY === '1'

// The app's error reporter is a side effect (a queued fetch to the run records),
// and the auth/session/router/toast singletons pull DOM-only modules — none are
// under test here, they only have to load so `axiosHttp` can build its instance.
vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))
vi.mock('@/services/account', () => ({ default: { loggedIn: false, accessToken: '' } }))
vi.mock('@/lib/session', () => ({ refreshSession: vi.fn() }))
vi.mock('@/router', () => ({ default: { replace: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

const envelope = (data: unknown, code = 200) => JSON.stringify({ code, message: 'ok', data })

describe.skipIf(!RUN)('task board upload: real wall-clock 30s / 60s boundary', () => {
  let server: Server
  let origin: string
  let axiosNewApi: typeof import('./axiosHttp').axiosNewApi
  let oldHttp: typeof import('@/network/api')
  // A mutable dial so each case decides how long the server holds the response.
  let delayMs = 45000
  const timers: NodeJS.Timeout[] = []
  const sockets = new Set<Socket>()

  beforeAll(async () => {
    server = createServer((req, res) => {
      req.on('error', () => undefined)
      res.on('error', () => undefined)
      // Drain the request body, then hold the answer for the current dial.
      req.resume()
      req.on('end', () => {
        const timer = setTimeout(() => {
          try {
            res.writeHead(201, { 'content-type': 'application/json' })
            res.end(envelope({ attachment: { id: 1 }, tasks: [] }))
          } catch {
            /* the client aborted the socket at its dial */
          }
        }, delayMs)
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

    // Stub before importing: the modules read `import.meta.env` once, at import.
    // Both the old tasks module and the new one use the newer origin.
    vi.stubEnv('VITE_API_BASE_URL', origin)
    vi.stubEnv('VITE_NEW_API_BASE_URL', origin)
    ;({ axiosNewApi } = await import('./axiosHttp'))
    oldHttp = await import('@/network/api')
    // The node environment has no XHR, but pin the adapter so the socket
    // timeout — not the MSW XHR shim — is what is measured.
    axiosNewApi.defaults.adapter = 'http'
    oldHttp.NewApiInstance.instance.defaults.adapter = 'http'
    oldHttp.default.instance.defaults.adapter = 'http'
  }, 120000)

  afterAll(async () => {
    timers.forEach((timer) => clearTimeout(timer))
    sockets.forEach((socket) => socket.destroy())
    await new Promise<void>((resolve) => server.close(() => resolve()))
    vi.unstubAllEnvs()
  })

  const file = () => new File(['%PDF-1.7'], 'notes.pdf', { type: 'application/pdf' })

  it('lets a 45s answer through on the 60s upload dial (new)', async () => {
    const { TaskBoardApi } = await import('./taskBoard')
    delayMs = 45000
    const started = Date.now()
    const result = await TaskBoardApi.uploadAttachment(7, file())
    const elapsed = Date.now() - started
    expect(result).toEqual({ attachment: { id: 1 }, tasks: [] })
    expect(elapsed).toBeGreaterThanOrEqual(45000)
    expect(elapsed).toBeLessThan(60000)
  }, 90000)

  it('lets a 45s answer through on the 60s upload dial (old)', async () => {
    const { TasksApi } = await import('@/network/api/tasks')
    delayMs = 45000
    const started = Date.now()
    const result = (await TasksApi.uploadAttachment(7, file())) as { data: unknown }
    const elapsed = Date.now() - started
    expect(result.data).toEqual({ attachment: { id: 1 }, tasks: [] })
    expect(elapsed).toBeGreaterThanOrEqual(45000)
    expect(elapsed).toBeLessThan(60000)
  }, 90000)

  it('aborts a 65s answer on the 30s default dial, before the server answers (new)', async () => {
    const { TaskBoardApi } = await import('./taskBoard')
    delayMs = 65000
    const started = Date.now()
    const error = (await TaskBoardApi.listAttachments(7).catch((failure) => failure)) as { message?: string }
    const elapsed = Date.now() - started
    expect(error).toBeInstanceOf(Error)
    expect(error.message).toMatch(/timeout of 30000ms/)
    expect(elapsed).toBeGreaterThanOrEqual(29000)
    expect(elapsed).toBeLessThan(60000)
  }, 90000)

  it('aborts a 65s answer on the 30s default dial, before the server answers (old)', async () => {
    const { TasksApi } = await import('@/network/api/tasks')
    delayMs = 65000
    const started = Date.now()
    const error = (await TasksApi.listAttachments(7).catch((failure) => failure)) as { message?: string }
    const elapsed = Date.now() - started
    expect(error).toBeInstanceOf(Error)
    expect(elapsed).toBeGreaterThanOrEqual(29000)
    expect(elapsed).toBeLessThan(60000)
  }, 90000)

  it('aborts a 65s answer on the 60s upload dial (new)', async () => {
    const { TaskBoardApi } = await import('./taskBoard')
    delayMs = 65000
    const started = Date.now()
    const error = (await TaskBoardApi.uploadAttachment(7, file()).catch((failure) => failure)) as {
      message?: string
    }
    const elapsed = Date.now() - started
    expect(error).toBeInstanceOf(Error)
    expect(error.message).toMatch(/timeout of 60000ms/)
    expect(elapsed).toBeGreaterThanOrEqual(59000)
    expect(elapsed).toBeLessThan(65000)
  }, 90000)

  it('aborts a 65s answer on the 60s upload dial (old)', async () => {
    const { TasksApi } = await import('@/network/api/tasks')
    delayMs = 65000
    const started = Date.now()
    const error = (await TasksApi.uploadAttachment(7, file()).catch((failure) => failure)) as {
      message?: string
    }
    const elapsed = Date.now() - started
    expect(error).toBeInstanceOf(Error)
    expect(error.message).toMatch(/timeout of 60000ms/)
    expect(elapsed).toBeGreaterThanOrEqual(59000)
    expect(elapsed).toBeLessThan(65000)
  }, 90000)
})
