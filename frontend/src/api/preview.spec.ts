import type { PreviewSelection, PreviewSession } from '../api'
import type { PreviewInfo } from '../cx_types'

import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { requestPreviewSession as fromBarrel } from '../api'

import { requestPreviewSession } from './preview'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

it('preserves the API barrel and sends the selected resource through authenticated fetch', async () => {
  const selection: PreviewSelection = { artifact_id: 'artifact', instance: 'listener' }
  const session: PreviewSession = {
    url: 'https://preview.example/_cheese/session',
    grant: 'grant',
    resource_id: 'resource',
    resource: { kind: 'app', path: 'http://localhost:5173', instance: 'listener' },
  }
  const info: PreviewInfo = {
    kind: 'app',
    path: 'http://localhost:5173',
    mime: 'application/x-cheese-app',
    artifact_id: selection.artifact_id,
    instance: selection.instance,
  }
  localStorage.setItem('accessToken', 'opaque-test-token')
  const fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ code: 200, data: session }), {
      headers: { 'content-type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetch)
  expect(fromBarrel).toBe(requestPreviewSession)
  expect(await fromBarrel('room/one', selection)).toEqual(session)
  expect(fetch).toHaveBeenCalledWith('/api/topics/room%2Fone/preview-session', {
    method: 'POST',
    body: JSON.stringify(selection),
    headers: expect.objectContaining({ Authorization: 'Bearer opaque-test-token', 'Content-Type': 'application/json' }),
  })
  expect(info.instance).toBe(session.resource?.instance)
})

it('preserves the legacy no-selection POST without an invented request body', async () => {
  const fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ code: 200, data: { url: 'https://preview.example/s', grant: 'grant' } }), {
      headers: { 'content-type': 'application/json' },
    })
  )
  vi.stubGlobal('fetch', fetch)
  await requestPreviewSession('room')
  expect(fetch.mock.calls[0]![0]).toBe('/api/topics/room/preview-session')
  expect(fetch.mock.calls[0]![1].method).toBe('POST')
  expect(fetch.mock.calls[0]![1]).not.toHaveProperty('body')
})

it('surfaces server rejection through the existing API error contract', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: 404, message: 'Preview instance gone' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      })
    )
  )
  await expect(requestPreviewSession('room', { instance: 'old' })).rejects.toMatchObject({ status: 404 })
})
