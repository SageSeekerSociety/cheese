// Unbinding an OAuth connection spends a sudo ticket, and the settings page
// tells the refusal to remove the last way in apart by its status.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, deleteOAuthConnection } from './api'

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

  it('rejects with an ApiError carrying the status and the backend name', async () => {
    vi.stubGlobal('fetch', async () =>
      reply(409, { code: 409, message: 'Last sign-in method', error: { name: 'ConflictError', message: 'x' } })
    )

    const refused = await deleteOAuthConnection('7', 5, 't').catch((e: unknown) => e)

    expect(refused).toBeInstanceOf(ApiError)
    expect((refused as ApiError).status).toBe(409)
    expect((refused as ApiError).code).toBe('ConflictError')
    // The sentence the page shows keeps the old shape — only now it has fields.
    expect((refused as ApiError).message).toContain('Last sign-in method')
    expect((refused as ApiError).message).toContain('HTTP 409')
  })
})
