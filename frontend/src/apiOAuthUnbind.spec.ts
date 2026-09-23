// Unbinding an OAuth connection spends a sudo ticket. The server's refusal
// has to reach the caller as SudoRequiredError, which is what sends the user
// through re-authentication instead of showing them an error.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SudoRequiredError } from './network/types/error'
import { deleteOAuthConnection } from './api'

function reply(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

describe('deleteOAuthConnection', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends the ticket with the request', async () => {
    const fetch = vi.fn(async () => reply(200, { code: 200, message: 'ok' }))
    vi.stubGlobal('fetch', fetch)

    await deleteOAuthConnection('7', 5, 'ticket-1')

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/users/7/oauth/connections/5')
    expect(init.method).toBe('DELETE')
    expect(JSON.parse(init.body as string)).toEqual({ sudoTicket: 'ticket-1' })
  })

  it('rejects with SudoRequiredError when the server asks for re-authentication', async () => {
    vi.stubGlobal('fetch', async () =>
      reply(403, {
        code: 403,
        message: 'Re-authentication required for this operation',
        error: { name: 'SudoRequiredError', message: 'Re-authentication required for this operation' },
      })
    )

    await expect(deleteOAuthConnection('7', 5, '')).rejects.toBeInstanceOf(SudoRequiredError)
  })

  it('keeps other refusals as plain errors', async () => {
    vi.stubGlobal('fetch', async () =>
      reply(409, { code: 409, message: 'Last sign-in method', error: { name: 'ConflictError', message: 'x' } })
    )

    const error = await deleteOAuthConnection('7', 5, 't').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(Error)
    expect(error).not.toBeInstanceOf(SudoRequiredError)
  })
})
