import { afterEach, expect, it, vi } from 'vitest'

import { ApiError, listTopics, READ_BUDGET_MS, refreshNow, RequestTimeoutError, TOKEN_REFRESH_BUDGET_MS } from './api'
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  localStorage.clear()
})
it('a hanging response body reaches the shared read deadline and aborts the fetch', async () => {
  vi.useFakeTimers()
  let signal: AbortSignal | undefined
  const fetcher = vi.fn((_url, init) => {
    signal = init.signal
    return Promise.resolve({ ok: true, status: 200, json: () => new Promise(() => {}) })
  })
  vi.stubGlobal('fetch', fetcher)
  const checked = expect(listTopics('p')).rejects.toBeInstanceOf(RequestTimeoutError)
  await vi.advanceTimersByTimeAsync(READ_BUDGET_MS)
  await checked
  expect(signal?.aborted).toBe(true)
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('a hanging refresh releases all waiters and allows another refresh', async () => {
  vi.useFakeTimers()
  const fetcher = vi
    .fn()
    .mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValue({ ok: true, json: async () => ({ data: { accessToken: 'renewed' } }) })
  vi.stubGlobal('fetch', fetcher)
  const waiting = [refreshNow(), refreshNow()]
  await vi.advanceTimersByTimeAsync(TOKEN_REFRESH_BUDGET_MS)
  await Promise.all(waiting)
  await refreshNow()
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(localStorage.getItem('accessToken')).toBe('renewed')
})
it('a known remote failure keeps its details and reaches the user without a second remote attempt', async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        message: 'GatewayUnavailableError: remote slow',
        error: { name: 'GatewayUnavailableError', message: '机器暂时无法响应', retryable: false },
      }),
      { status: 503, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'trace-1' } }
    )
  )
  vi.stubGlobal('fetch', fetcher)
  try {
    await listTopics('p')
    throw new Error('expected failure')
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      message: '机器暂时无法响应',
      code: 'GatewayUnavailableError',
      requestId: 'trace-1',
      retryable: false,
    })
  }
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('retries share the original read deadline', async () => {
  vi.useFakeTimers()
  const fetcher = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () =>
              resolve(
                new Response(JSON.stringify({ message: 'gateway' }), {
                  status: 503,
                  headers: { 'Content-Type': 'application/json' },
                })
              ),
            19000
          )
        )
    )
    .mockImplementation(() => new Promise(() => {}))
  vi.stubGlobal('fetch', fetcher)
  const checked = expect(listTopics('p')).rejects.toBeInstanceOf(RequestTimeoutError)
  await vi.advanceTimersByTimeAsync(READ_BUDGET_MS)
  await checked
  expect(fetcher).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(READ_BUDGET_MS)
  expect(fetcher).toHaveBeenCalledTimes(2)
})
