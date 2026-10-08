// The connector's client speaks plain JSON (no envelope), and a refusal it
// raises still has to carry the fields a caller switches on.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { connectorRequest } from './connector'
import { ApiError } from './http'

function reply(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

async function refused(): Promise<ApiError> {
  vi.stubGlobal('fetch', async () =>
    reply(401, {
      code: 401,
      message: '需要登录才能管理设备',
      error: { name: 'UnauthorizedError', message: '需要登录才能管理设备', data: null, retryable: false },
    })
  )
  return connectorRequest('/my/devices').then(
    () => {
      throw new Error('expected a refusal')
    },
    (e: unknown) => e as ApiError
  )
}

describe('connectorRequest', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('rejects with an ApiError carrying the status and the backend name', async () => {
    const e = await refused()

    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(401)
    expect(e.code).toBe('UnauthorizedError')
    expect(e.message).toBe('需要登录才能管理设备')
  })
})
