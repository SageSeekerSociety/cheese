// @vitest-environment jsdom
// The teams domain's authentication contract, split out of
// `teamsContract.spec.ts` to keep each file under the src/api size cap. Teams
// rides the new origin, so this pins how the new transport (`axiosNewApi`)
// handles the 401 refresh/redirect flow — no bearer when the caller is signed
// out or holds an empty token, one refresh-and-retry, and what happens when the
// refresh is rejected or unreachable. The request and payload contract, the
// refusals and the empty-command success bit are in `teamsContract.spec.ts`.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { auth, contractEnvironment, envelope, newer, refresh, replace, server } from './requestContract.spec.helper'

// The reporter is a side effect (a queued fetch to the run records), not a
// method under test: silence it so a refusal we deliberately send does not
// leave the suite with an unhandled request.
vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))

contractEnvironment()

let newTeams: typeof import('./teams').TeamsApi

beforeAll(async () => {
  ;({ TeamsApi: newTeams } = await import('./teams'))
})

describe('teams: authentication edge cases', () => {
  // The 401 refresh/redirect flow lives in the new transport (`axiosNewApi`).
  const unauthorized = () => {
    let requests = 0
    server.use(
      http.get(`${newer}/teams/7`, ({ request }) => {
        requests++
        expect(request.url.startsWith(`${newer}/teams/7`)).toBe(true)
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
        http.get(`${newer}/teams/7`, ({ request }) => {
          expect(request.headers.get('authorization')).toBeNull()
          return HttpResponse.json(envelope({ team: { id: 7 } }))
        })
      )
      expect(await newTeams.detail(7)).toEqual({ team: { id: 7 } })
    }
  })

  it('refreshes once and retries with the returned token', async () => {
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    const authorization: (string | null)[] = []
    server.use(
      http.get(`${newer}/teams/7`, ({ request }) => {
        expect(request.url.startsWith(`${newer}/teams/7`)).toBe(true)
        authorization.push(request.headers.get('authorization'))
        if (authorization.length === 1) return new HttpResponse(null, { status: 401 })
        return HttpResponse.json(envelope({ team: { id: 7 } }))
      })
    )
    expect(await newTeams.detail(7)).toEqual({ team: { id: 7 } })
    expect(authorization).toEqual(['Bearer contract-token', 'Bearer refreshed-token'])
    expect(refresh).toHaveBeenCalledOnce()
  })

  it('redirects to sign-in when the refresh is rejected, without a retry', async () => {
    const count = unauthorized()
    refresh.mockReset().mockResolvedValue({ kind: 'rejected' })
    replace.mockReset()
    await expect(newTeams.detail(7)).rejects.toBeInstanceOf(Error)
    expect(count()).toBe(1)
    expect(replace).toHaveBeenCalledWith('/account/signin')
  })

  it('does not redirect when the refresh is unreachable', async () => {
    const count = unauthorized()
    refresh.mockReset().mockResolvedValue({ kind: 'unreachable' })
    replace.mockReset()
    await expect(newTeams.detail(7)).rejects.toBeInstanceOf(Error)
    expect(count()).toBe(1)
    expect(replace).not.toHaveBeenCalled()
  })

  it('stops after a refreshed request is still unauthorized', async () => {
    const count = unauthorized()
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    const error = await newTeams.detail(7).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
    expect(count()).toBe(2)
    expect(refresh).toHaveBeenCalledOnce()
  })
})
