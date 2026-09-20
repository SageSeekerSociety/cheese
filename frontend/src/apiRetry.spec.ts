import { describe, expect, it } from 'vitest'

import { isRetryableGetFailure } from './api'

describe('API GET retry policy', () => {
  it('retries transient gateway and network failures', () => {
    expect(isRetryableGetFailure('GET', 502)).toBe(true)
    expect(isRetryableGetFailure('GET', 503)).toBe(true)
    expect(isRetryableGetFailure('GET', 504)).toBe(true)
    expect(isRetryableGetFailure('GET', undefined, new TypeError('network'))).toBe(true)
  })

  it("retries Cloudflare's own statuses, and an error page whatever its status", () => {
    expect(isRetryableGetFailure('GET', 520)).toBe(true)
    expect(isRetryableGetFailure('GET', 530)).toBe(true)
    expect(isRetryableGetFailure('GET', 200, undefined, true)).toBe(true)
    expect(isRetryableGetFailure('GET', 531)).toBe(false)
    expect(isRetryableGetFailure('GET', 500)).toBe(false)
  })

  it('does not retry writes, permanent responses, or aborts', () => {
    expect(isRetryableGetFailure('POST', 503)).toBe(false)
    expect(isRetryableGetFailure('POST', 530)).toBe(false)
    expect(isRetryableGetFailure('POST', 200, undefined, true)).toBe(false)
    expect(isRetryableGetFailure('GET', 401)).toBe(false)
    expect(isRetryableGetFailure('GET', undefined, new DOMException('', 'AbortError'))).toBe(false)
  })
})
