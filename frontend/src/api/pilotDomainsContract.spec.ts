// @vitest-environment jsdom
// The four-domain pilot's request contract: every wire request these four API
// domains send is pinned here, one case per call. Notifications' unread-count
// read and a task's attachment list ride the new origin (`newer`); the avatar
// upload and the material upload ride the legacy/primary one, and each names
// its own multipart fields (the shared runner only knows a single `file`).
// Around those requests sit the refusal, edge-page, lost-network and
// 401/refresh cases the pilot exists to protect.
import type { Mock } from 'vitest'

import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { transportFailureMessage } from '../lib/transportFailure'

import {
  auth,
  contractEnvironment,
  contractRequest,
  envelope,
  newer,
  primary,
  refresh,
  replace,
  server,
} from './requestContract.spec.helper'

contractEnvironment()

let avatars: typeof import('./avatars')
let notifications: typeof import('./notifications')
let taskFiles: typeof import('./taskFiles')
let materials: typeof import('./materials')
let newErrorMessage: typeof import('./axiosHttp').axiosErrorMessage
let notified: Mock

beforeAll(async () => {
  avatars = await import('./avatars')
  notifications = await import('./notifications')
  taskFiles = await import('./taskFiles')
  materials = await import('./materials')
  ;({ axiosErrorMessage: newErrorMessage } = await import('./axiosHttp'))
  // The session-expired toast is the mock `requestContract.spec.helper` installs.
  notified = vi.mocked((await import('vuetify-sonner')).toast.error)
})

const png = () => new File([new Uint8Array([137, 80, 78, 71])], 'avatar.png', { type: 'image/png' })
const pdf = () => new File(['%PDF-1.7'], 'notes.pdf', { type: 'application/pdf' })

function reads() {
  return [
    {
      base: newer,
      path: '/notifications/unread-count',
      args: [],
      payload: { count: 7 },
      next: () => notifications.getUnreadNotificationCount(),
    },
    {
      base: newer,
      path: '/tasks/12/files',
      args: [12],
      payload: {
        files: [{ name: 'notes.pdf', size: 8, addedAt: 0 }],
        canDownload: false,
      },
      next: () => taskFiles.listTaskFiles(12),
    },
  ]
}

describe('four-domain pilot against the HTTP contract', () => {
  it('reads the authenticated payload without adding a query or body', async () => {
    for (const read of reads()) {
      await contractRequest({ method: 'GET', path: read.path, args: read.args }, read.next, read.base)
    }
  })

  it('does not infer download permission from the presence of materials', async () => {
    const read = reads()[1]
    for (const canDownload of [false, true]) {
      server.use(
        http.get(`${read.base}${read.path}`, () => HttpResponse.json(envelope({ ...read.payload, canDownload })))
      )
      expect(await read.next()).toMatchObject({ canDownload })
    }
  })

  it('uploads the named file fields and unwraps the real 201 envelope', async () => {
    const uploads = [
      {
        path: `${primary}/avatars`,
        field: 'avatar',
        file: png(),
        payload: { avatarId: 42 },
        next: (file: File) => avatars.createAvatar(file),
        type: null,
      },
      {
        path: `${primary}/materials`,
        field: 'file',
        file: pdf(),
        payload: { id: 99 },
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
          bodies.push({ name: file.name, type: file.type, bytes, kind: body.get('type') })
          return HttpResponse.json(envelope(upload.payload, 201), { status: 201 })
        })
      )
      expect(await upload.next(upload.file)).toEqual(upload.payload)
      expect(bodies).toEqual([
        {
          name: upload.file.name,
          type: upload.file.type,
          bytes: [...new Uint8Array(await upload.file.arrayBuffer())],
          kind: upload.type,
        },
      ])
    }
  })

  it.each([400, 403, 404, 422, 500])('keeps a %s refusal and its business sentence/data', async (status) => {
    for (const read of reads()) {
      server.use(
        http.get(`${read.base}${read.path}`, () =>
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
      const next = await read.next().catch((error) => error)
      expect(next).toMatchObject({
        message: 'You cannot read this',
        status,
        code: 'NoAccess',
        data: { reason: 'membership' },
      })
      expect(newErrorMessage(next, 'fallback')).toBe('You cannot read this')
    }
  })

  it('does not retry rejected uploads or create a successful empty result', async () => {
    for (const upload of [
      { path: `${primary}/avatars`, call: () => avatars.createAvatar(png()) },
      { path: `${primary}/materials`, call: () => materials.uploadMaterial(pdf(), 'file') },
    ]) {
      const seen: string[] = []
      server.use(
        http.post(upload.path, ({ request }) => {
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
      await expect(upload.call()).rejects.toMatchObject({ message: 'file rejected' })
      expect(seen).toHaveLength(1)
    }
  })

  it('uses the transport fallback for HTML from an edge, including a 200 page', async () => {
    for (const status of [200, 530]) {
      const read = reads()[0]
      let requests = 0
      server.use(
        http.get(`${read.base}${read.path}`, () => {
          requests++
          return new HttpResponse('<html>offline</html>', { status, headers: { 'Content-Type': 'text/html' } })
        })
      )
      const next = await read.next().catch((error) => error)
      expect(next).toBeInstanceOf(Error)
      expect((next as Error).message).toBe(transportFailureMessage('get', status))
      expect(newErrorMessage(next, 'fallback')).toBe(transportFailureMessage('get', status))
      expect(requests).toBe(1)
    }
  })

  it('refreshes a 401 once and retries with the returned token', async () => {
    const read = reads()[0]
    server.resetHandlers()
    auth.accessToken = 'contract-token'
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    const authorization: (string | null)[] = []
    let requests = 0
    server.use(
      http.get(`${read.base}${read.path}`, ({ request }) => {
        requests++
        authorization.push(request.headers.get('authorization'))
        if (requests === 1) return new HttpResponse(null, { status: 401 })
        return HttpResponse.json(envelope({ count: 8 }))
      })
    )
    expect(await read.next()).toEqual({ count: 8 })
    expect(requests).toBe(2)
    expect(authorization).toEqual(['Bearer contract-token', 'Bearer refreshed-token'])
    expect(refresh).toHaveBeenCalledOnce()
  })

  // A renewal that fails — turned down or out of reach — is attempted once, and
  // that is all this layer owes: no retry, no notice, no navigation. Ending the
  // sign-in moved to `lib/session.ts` (the announcement) and
  // `services/account.ts` (the session drop and the sign-in), for both stacks
  // (`account.sessionOver.spec.ts`).
  it.each([
    ['rejected', { kind: 'rejected' as const }],
    ['unreachable', { kind: 'unreachable' as const }],
  ] as const)('does not retry a 401 when refresh is %s', async (_label, outcome) => {
    const read = reads()[0]
    server.resetHandlers()
    refresh.mockReset().mockResolvedValue(outcome)
    notified.mockClear()
    let requests = 0
    server.use(
      http.get(`${read.base}${read.path}`, () => {
        requests++
        return new HttpResponse(null, { status: 401 })
      })
    )
    await expect(read.next()).rejects.toBeInstanceOf(Error)
    expect(requests).toBe(1)
    expect(refresh).toHaveBeenCalledOnce()
    expect(replace).not.toHaveBeenCalled()
    expect(notified).not.toHaveBeenCalled()
  })

  it('stops after a refreshed request is still unauthorized, including POST uploads', async () => {
    const endpoints = [
      { path: `${reads()[0].base}${reads()[0].path}`, method: http.get, call: reads()[0].next },
      { path: `${reads()[1].base}${reads()[1].path}`, method: http.get, call: reads()[1].next },
      { path: `${primary}/avatars`, method: http.post, call: () => avatars.createAvatar(png()) },
      { path: `${primary}/materials`, method: http.post, call: () => materials.uploadMaterial(pdf(), 'file') },
    ]
    for (const endpoint of endpoints) {
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
      const error = await endpoint.call().catch((failure) => failure)
      expect(error).toBeInstanceOf(Error)
      expect(newErrorMessage(error, 'fallback')).toBe('fallback')
      expect(requests).toBe(2)
      expect(refresh).toHaveBeenCalledOnce()
    }
  })

  it.each([403, 500])('keeps an unnamed %s server sentence visible', async (status) => {
    const read = reads()[0]
    server.use(
      http.get(`${read.base}${read.path}`, () =>
        HttpResponse.json({ code: status, message: 'Server sentence', data: null }, { status })
      )
    )
    const next = await read.next().catch((error) => error)
    expect(newErrorMessage(next, 'fallback')).toBe('Server sentence')
  })

  it('uses the caller fallback after a network failure without retrying', async () => {
    const read = reads()[0]
    let requests = 0
    server.use(
      http.get(`${read.base}${read.path}`, () => {
        requests++
        return HttpResponse.error()
      })
    )
    const next = await read.next().catch((error) => error)
    expect(next).toBeInstanceOf(Error)
    expect(newErrorMessage(next, 'fallback')).toBe('fallback')
    expect(requests).toBe(1)
  })

  it('sends no bearer when the stored account is not logged in', async () => {
    auth.loggedIn = false
    server.use(
      http.get(`${newer}/notifications/unread-count`, ({ request }) => {
        expect(request.headers.get('authorization')).toBeNull()
        return HttpResponse.json(envelope({ count: 0 }))
      })
    )
    expect(await notifications.getUnreadNotificationCount()).toEqual({ count: 0 })
  })
})
