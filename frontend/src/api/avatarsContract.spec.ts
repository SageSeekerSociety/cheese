// @vitest-environment jsdom
// The avatars domain's request contract. `createAvatar` (a multipart POST) is
// covered by the pilot in `pilotDomainsContract.spec.ts`; this is the read it
// never covered — `getDefaultAvatarId`. Avatars ride the legacy (primary)
// origin, so the handler answers from `${primary}`.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it } from 'vitest'

import { contractEnvironment, contractRequest, envelope, primary, server } from './requestContract.spec.helper'

contractEnvironment()

let avatars: typeof import('./avatars')

beforeAll(async () => {
  avatars = await import('./avatars')
})

describe('avatars: the default avatar id keeps its request and payload', () => {
  it('reads the default avatar id from the payload without a query or body', async () => {
    await contractRequest(
      { method: 'GET', path: '/avatars/default/id', args: [] },
      () => avatars.getDefaultAvatarId(),
      primary
    )
  })

  it('keeps a falsy payload unchanged rather than inventing an id', async () => {
    server.use(http.get(`${primary}/avatars/default/id`, () => HttpResponse.json(envelope(null))))
    expect(await avatars.getDefaultAvatarId()).toBeNull()
  })
})
