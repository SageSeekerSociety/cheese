// @vitest-environment jsdom
import { Blob as NodeBlob, File as NodeFile } from 'node:buffer'

import type { AxiosInstance } from 'axios'

import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ loggedIn: true, accessToken: 'contract-token' }))
const refresh = vi.hoisted(() => vi.fn())
const replace = vi.hoisted(() => vi.fn())
const expired = vi.hoisted(() => vi.fn())
vi.mock('@/services/account', () => ({ default: auth }))
vi.mock('@/lib/session', () => ({ refreshSession: refresh }))
vi.mock('@/router', () => ({ default: { replace } }))
vi.mock('vuetify-sonner', () => ({ toast: { error: expired } }))

const primary = 'https://primary.example.test/api'
const newer = 'https://newer.example.test/api'
const server = setupServer()
let oldAvatars: typeof import('@/network/api/avatars').AvatarsApi
let oldNotifications: typeof import('@/network/api/notifications').NotificationsApi
let oldTasks: typeof import('@/network/api/tasks').TasksApi
let oldMaterials: typeof import('@/network/api/materials').MaterialsApi
let avatars: typeof import('./avatars')
let notifications: typeof import('./notifications')
let attachments: typeof import('./taskAttachments')
let materials: typeof import('./materials')
let legacyErrorMessage: typeof import('@/network/utils/requestErrorMessage').requestErrorMessage
let newErrorMessage: typeof import('./axiosHttp').axiosErrorMessage
let instances: AxiosInstance[]
let originalAdapters: AxiosInstance['defaults']['adapter'][]

const envelope = (data: unknown, code = 200) => ({ code, message: 'ok', data })
const png = () => new File([new Uint8Array([137, 80, 78, 71])], 'avatar.png', { type: 'image/png' })
const pdf = () => new File(['%PDF-1.7'], 'notes.pdf', { type: 'application/pdf' })

beforeAll(async () => {
  // Keep jsdom for the browser adapter, but use one undici realm for multipart values.
  const nativeFormData = (
    await new Request('https://form-data.example.test', {
      method: 'POST',
      body: 'probe=value',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
    }).formData()
  ).constructor
  vi.stubGlobal('FormData', nativeFormData)
  vi.stubGlobal('File', NodeFile)
  vi.stubGlobal('Blob', NodeBlob)
  vi.stubEnv('VITE_API_BASE_URL', primary)
  vi.stubEnv('VITE_NEW_API_BASE_URL', newer)
  server.listen({ onUnhandledRequest: 'error' })
  const oldHttp = await import('@/network/api')
  const newHttp = await import('./axiosHttp')
  instances = [oldHttp.default.instance, oldHttp.NewApiInstance.instance, newHttp.axiosApi, newHttp.axiosNewApi]
  // Exercise the browser XHR adapter; application-wide defaults stay untouched.
  originalAdapters = instances.map((instance) => instance.defaults.adapter)
  for (const instance of instances) instance.defaults.adapter = 'xhr'
  ;({ AvatarsApi: oldAvatars } = await import('@/network/api/avatars'))
  ;({ NotificationsApi: oldNotifications } = await import('@/network/api/notifications'))
  ;({ TasksApi: oldTasks } = await import('@/network/api/tasks'))
  ;({ MaterialsApi: oldMaterials } = await import('@/network/api/materials'))
  avatars = await import('./avatars')
  notifications = await import('./notifications')
  attachments = await import('./taskAttachments')
  materials = await import('./materials')
  ;({ requestErrorMessage: legacyErrorMessage } = await import('@/network/utils/requestErrorMessage'))
  newErrorMessage = newHttp.axiosErrorMessage
})
beforeEach(() => {
  auth.loggedIn = true
  auth.accessToken = 'contract-token'
  refresh.mockReset().mockResolvedValue({ kind: 'unreachable' })
  replace.mockReset()
  expired.mockReset()
})
afterEach(() => server.resetHandlers())
afterAll(() => {
  instances.forEach((instance, index) => {
    instance.defaults.adapter = originalAdapters[index]
  })
  server.close()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

function reads() {
  return [
    {
      path: `${newer}/notifications/unread-count`,
      payload: { count: 7 },
      old: () => oldNotifications.getUnreadCount().then((res) => res.data),
      next: () => notifications.getUnreadNotificationCount(),
    },
    {
      path: `${newer}/tasks/12/attachments`,
      payload: {
        attachments: [
          { id: 3, name: 'notes.pdf', size: 8, contentType: 'application/pdf', downloadCount: 2, createdAt: 0 },
        ],
        canDownload: false,
      },
      old: () => oldTasks.listAttachments(12).then((res) => res.data),
      next: () => attachments.listTaskAttachments(12),
    },
  ]
}

describe('four-domain migration against the HTTP contract', () => {
  it('reads the authenticated payload without adding a query or body', async () => {
    for (const read of reads()) {
      const seen: string[] = []
      server.use(
        http.get(read.path, async ({ request }) => {
          expect(request.url).toBe(read.path)
          expect(request.headers.get('authorization')).toBe('Bearer contract-token')
          seen.push(request.url)
          return HttpResponse.json(envelope(read.payload))
        })
      )
      expect(await read.old()).toEqual(read.payload)
      expect(await read.next()).toEqual(read.payload)
      expect(seen).toHaveLength(2)
    }
  })

  it('does not infer download permission from the presence of attachments', async () => {
    const read = reads()[1]
    for (const canDownload of [false, true]) {
      server.use(http.get(read.path, () => HttpResponse.json(envelope({ ...read.payload, canDownload }))))
      expect(await read.old()).toMatchObject({ canDownload })
      expect(await read.next()).toMatchObject({ canDownload })
    }
  })

  it('uploads the same named file fields and unwraps real 201 envelopes', async () => {
    const uploads = [
      {
        path: `${primary}/avatars`,
        field: 'avatar',
        file: png(),
        payload: { avatarId: 42 },
        old: (file: File) => oldAvatars.createAvatar(file).then((res) => res.data),
        next: (file: File) => avatars.createAvatar(file),
        type: null,
      },
      {
        path: `${primary}/materials`,
        field: 'file',
        file: pdf(),
        payload: { id: 99 },
        old: (file: File) => oldMaterials.upload(file, 'file').then((res) => res.data),
        next: (file: File) => materials.uploadMaterial(file, 'file'),
        type: 'file',
      },
    ]
    for (const upload of uploads) {
      const bodies: unknown[] = []
      server.use(
        http.post(upload.path, async ({ request }) => {
          expect(request.headers.get('authorization')).toBe('Bearer contract-token')
          expect(request.headers.get('content-type')).toContain('multipart/form-data; boundary=')
          const body = await request.formData()
          expect([...body.keys()].sort()).toEqual(upload.type ? ['file', 'type'] : ['avatar'])
          const file = body.get(upload.field) as File
          const bytes = [...new Uint8Array(await file.arrayBuffer())]
          const record = { name: file.name, type: file.type, bytes, kind: body.get('type') }
          expect(record).toEqual({
            name: upload.file.name,
            type: upload.file.type,
            bytes: [...new Uint8Array(await upload.file.arrayBuffer())],
            kind: upload.type,
          })
          bodies.push(record)
          return HttpResponse.json(envelope(upload.payload, 201), { status: 201 })
        })
      )
      expect(await upload.old(upload.file)).toEqual(upload.payload)
      expect(await upload.next(upload.file)).toEqual(upload.payload)
      expect(bodies).toHaveLength(2)
      expect(bodies[1]).toEqual(bodies[0])
    }
  })

  it.each([400, 403, 404, 422, 500])('keeps a %s refusal and its business sentence/data', async (status) => {
    for (const read of reads()) {
      server.use(
        http.get(read.path, () =>
          HttpResponse.json(
            {
              code: status,
              message: 'refused',
              data: null,
              error: { name: 'NoAccess', message: 'You cannot read this', data: { reason: 'membership' } },
            },
            { status }
          )
        )
      )
      const old = await read.old().catch((error) => error)
      const next = await read.next().catch((error) => error)
      expect(old).toMatchObject({
        message: 'You cannot read this',
        code: status,
        error: { name: 'NoAccess', data: { reason: 'membership' } },
      })
      expect(next).toMatchObject({
        message: 'You cannot read this',
        status,
        code: 'NoAccess',
        data: { reason: 'membership' },
      })
      expect(legacyErrorMessage(old, 'fallback')).toBe('You cannot read this')
      expect(newErrorMessage(next, 'fallback')).toBe('You cannot read this')
    }
  })

  it('does not retry rejected uploads or create a successful empty result', async () => {
    for (const path of [`${primary}/avatars`, `${primary}/materials`]) {
      const seen: string[] = []
      server.use(
        http.post(path, ({ request }) => {
          seen.push(request.url)
          return HttpResponse.json(
            {
              code: 422,
              message: 'file rejected',
              data: null,
              error: { name: 'InvalidFile', message: 'file rejected' },
            },
            { status: 422 }
          )
        })
      )
      const calls = path.endsWith('/avatars')
        ? [() => oldAvatars.createAvatar(png()), () => avatars.createAvatar(png())]
        : [() => oldMaterials.upload(pdf(), 'file'), () => materials.uploadMaterial(pdf(), 'file')]
      for (const call of calls) await expect(call()).rejects.toMatchObject({ message: 'file rejected' })
      expect(seen).toHaveLength(2)
    }
  })

  it('uses the transport fallback for HTML from an edge, including a 200 page', async () => {
    for (const status of [200, 530]) {
      const read = reads()[0]
      let requests = 0
      server.use(
        http.get(read.path, () => {
          requests++
          return new HttpResponse('<html>offline</html>', { status, headers: { 'Content-Type': 'text/html' } })
        })
      )
      const old = await read.old().catch((error) => error)
      const next = await read.next().catch((error) => error)
      expect(old).toBeInstanceOf(Error)
      expect(next).toBeInstanceOf(Error)
      expect(next.message).toBe(old.message)
      expect(legacyErrorMessage(old, 'fallback')).toBe(old.message)
      expect(newErrorMessage(next, 'fallback')).toBe(legacyErrorMessage(old, 'fallback'))
      expect(requests).toBe(2)
    }
  })

  it('refreshes a 401 once and retries with the returned token', async () => {
    const read = reads()[0]
    for (const call of [read.old, read.next]) {
      server.resetHandlers()
      auth.accessToken = 'contract-token'
      refresh.mockReset().mockImplementation(async () => {
        auth.accessToken = 'refreshed-token'
        return { kind: 'ok', token: 'refreshed-token' }
      })
      const authorization: (string | null)[] = []
      let requests = 0
      server.use(
        http.get(read.path, ({ request }) => {
          requests++
          authorization.push(request.headers.get('authorization'))
          if (requests === 1) return new HttpResponse(null, { status: 401 })
          return HttpResponse.json(envelope({ count: 8 }))
        })
      )
      const result = await call()
      expect(result).toEqual({ count: 8 })
      expect(requests).toBe(2)
      expect(authorization).toEqual(['Bearer contract-token', 'Bearer refreshed-token'])
      expect(refresh).toHaveBeenCalledOnce()
    }
  })

  it.each([
    ['rejected', { kind: 'rejected' as const }, true],
    ['unreachable', { kind: 'unreachable' as const }, false],
  ] as const)('does not retry a 401 when refresh is %s', async (_label, outcome, redirects) => {
    const read = reads()[0]
    for (const call of [read.old, read.next]) {
      server.resetHandlers()
      refresh.mockReset().mockResolvedValue(outcome)
      replace.mockReset()
      expired.mockReset()
      let requests = 0
      server.use(
        http.get(read.path, () => {
          requests++
          return new HttpResponse(null, { status: 401 })
        })
      )
      await expect(call()).rejects.toBeInstanceOf(Error)
      expect(requests).toBe(1)
      expect(refresh).toHaveBeenCalledOnce()
      expect(replace).toHaveBeenCalledTimes(redirects ? 1 : 0)
      expect(expired).toHaveBeenCalledTimes(redirects ? 1 : 0)
    }
  })

  it('stops after a refreshed request is still unauthorized, including POST uploads', async () => {
    const endpoints = [
      { path: reads()[0].path, method: http.get, calls: [reads()[0].old, reads()[0].next] },
      { path: reads()[1].path, method: http.get, calls: [reads()[1].old, reads()[1].next] },
      {
        path: `${primary}/avatars`,
        method: http.post,
        calls: [() => oldAvatars.createAvatar(png()), () => avatars.createAvatar(png())],
      },
      {
        path: `${primary}/materials`,
        method: http.post,
        calls: [() => oldMaterials.upload(pdf(), 'file'), () => materials.uploadMaterial(pdf(), 'file')],
      },
    ]
    for (const endpoint of endpoints) {
      for (const call of endpoint.calls) {
        refresh.mockReset().mockImplementation(async () => {
          auth.accessToken = 'refreshed-token'
          return { kind: 'ok', token: 'refreshed-token' }
        })
        let requests = 0
        server.use(
          endpoint.method(endpoint.path, () => {
            requests++
            return new HttpResponse(null, { status: 401 })
          })
        )
        const error = await call().catch((failure) => failure)
        expect(error).toBeInstanceOf(Error)
        expect(newErrorMessage(error, 'fallback')).toBe('fallback')
        expect(legacyErrorMessage(error, 'fallback')).toBe('fallback')
        expect(requests).toBe(2)
        expect(refresh).toHaveBeenCalledOnce()
      }
    }
  })

  it.each([403, 500])('keeps an unnamed %s server sentence visible', async (status) => {
    const read = reads()[0]
    server.use(
      http.get(read.path, () => HttpResponse.json({ code: status, message: 'Server sentence', data: null }, { status }))
    )
    const old = await read.old().catch((error) => error)
    const next = await read.next().catch((error) => error)
    expect(legacyErrorMessage(old, 'fallback')).toBe('Server sentence')
    expect(newErrorMessage(next, 'fallback')).toBe('Server sentence')
  })

  it('uses the caller fallback after a network failure without retrying', async () => {
    const read = reads()[0]
    let requests = 0
    server.use(
      http.get(read.path, () => {
        requests++
        return HttpResponse.error()
      })
    )
    const old = await read.old().catch((error) => error)
    const next = await read.next().catch((error) => error)
    expect(old).toBeInstanceOf(Error)
    expect(next).toBeInstanceOf(Error)
    expect(legacyErrorMessage(old, 'fallback')).toBe('fallback')
    expect(newErrorMessage(next, 'fallback')).toBe('fallback')
    expect(requests).toBe(2)
  })

  it('sends no bearer when the stored account is not logged in', async () => {
    auth.loggedIn = false
    server.use(
      http.get(`${newer}/notifications/unread-count`, ({ request }) => {
        expect(request.headers.get('authorization')).toBeNull()
        return HttpResponse.json(envelope({ count: 0 }))
      })
    )
    expect(await oldNotifications.getUnreadCount()).toMatchObject({ data: { count: 0 } })
    expect(await notifications.getUnreadNotificationCount()).toEqual({ count: 0 })
  })
})
