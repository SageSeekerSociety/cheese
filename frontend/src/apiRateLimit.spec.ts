// A 429 from the backend's per-client limits: a read waits as long as it was
// told and asks once more; anything else tells the user what happened.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, request } from './api'
import { t } from './i18n'

const QUOTA_EXCEEDED = 'https://iana.org/assignments/http-problem-types#quota-exceeded'

function refused(retryAfter: string): Response {
  return new Response(
    JSON.stringify({
      code: 429,
      message: 'QuotaExceededError: Too many requests',
      error: { name: 'QuotaExceededError', message: 'Too many requests', retryable: true, data: null },
      type: QUOTA_EXCEEDED,
      'violated-policies': ['rate'],
    }),
    { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': retryAfter } }
  )
}

function ok(): Response {
  return new Response(JSON.stringify({ code: 200, message: 'ok', data: 'fine' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('a request refused for coming too fast', () => {
  let answers: Response[]
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    localStorage.clear()
    answers = []
    fetchMock = vi.fn(async () => answers.shift() ?? ok())
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('waits out Retry-After and reads again', async () => {
    vi.useFakeTimers()
    answers = [refused('2'), ok()]
    const read = request<string>('/projects')
    await vi.advanceTimersByTimeAsync(1_999)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await expect(read).resolves.toBe('fine')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('asks only once more, then says why', async () => {
    answers = [refused('0'), refused('3')]
    const error = await request('/projects').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(429)
    expect((error as ApiError).message).toBe(t('global.request.rateLimited', { seconds: 3 }))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not repeat a write', async () => {
    answers = [refused('4')]
    const error = await request('/projects', { method: 'POST', body: '{}' }).catch((e: unknown) => e)
    expect((error as ApiError).message).toBe(t('global.request.rateLimited', { seconds: 4 }))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not wait longer than the read budget allows', async () => {
    answers = [refused('60')]
    const error = await request('/projects').catch((e: unknown) => e)
    expect((error as ApiError).status).toBe(429)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('keeps the words of a 429 that is not the request limit', async () => {
    answers = [
      new Response(JSON.stringify({ code: 429, message: 'still answering the last question' }), {
        status: 429,
        headers: { 'Content-Type': 'application/json' },
      }),
    ]
    const error = await request('/assistant', { method: 'POST', body: '{}' }).catch((e: unknown) => e)
    expect((error as ApiError).message).toBe('still answering the last question')
  })
})
