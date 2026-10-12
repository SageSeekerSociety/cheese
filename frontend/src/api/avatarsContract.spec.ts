// @vitest-environment jsdom
// The avatars domain's payload contract. `createAvatar` (a multipart POST) is
// already compared in the pilot `migrationContract.spec.ts`; this adds the
// read it never covered — `getDefaultAvatarId` — with a real old-versus-new
// comparison. Avatars ride the legacy (primary) origin, so both sides answer
// from `${primary}`.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it } from 'vitest'

import { compareRequests, contractEnvironment, envelope, primary, server } from './migrationContract.spec.helper'

contractEnvironment()

let oldAvatars: typeof import('@/network/api/avatars').AvatarsApi
let avatars: typeof import('./avatars')

beforeAll(async () => {
  ;({ AvatarsApi: oldAvatars } = await import('@/network/api/avatars'))
  avatars = await import('./avatars')
})

describe('avatars migration: the default avatar id keeps its request and payload', () => {
  it('reads the default avatar id from the payload without a query or body', async () => {
    await compareRequests(
      { method: 'GET', path: '/avatars/default/id', args: [] },
      () => oldAvatars.getDefaultAvatarId(),
      () => avatars.getDefaultAvatarId(),
      primary
    )
  })

  it('keeps a falsy payload unchanged rather than inventing an id', async () => {
    server.use(http.get(`${primary}/avatars/default/id`, () => HttpResponse.json(envelope(null))))
    const old = await oldAvatars.getDefaultAvatarId()
    const next = await avatars.getDefaultAvatarId()
    expect(old).toEqual({ code: 200, message: 'ok', data: null })
    expect(next).toBeNull()
  })
})
