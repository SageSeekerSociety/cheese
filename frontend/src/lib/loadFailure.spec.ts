import { describe, expect, it } from 'vitest'

import { isForbidden, loadFailureReason } from './loadFailure'

import { ApiError } from '@/api/http'

describe('isForbidden', () => {
  it('403 是没权限', () => {
    expect(isForbidden(new ApiError(403, 'HTTP 403 for /library'))).toBe(true)
  })

  it('401 也算没权限：再试一次还是没登录', () => {
    expect(isForbidden(new ApiError(401, 'HTTP 401 for /library'))).toBe(true)
  })

  it('别的失败不算：它们值得再试一次', () => {
    expect(isForbidden(new ApiError(500, 'HTTP 500 for /library'))).toBe(false)
    expect(isForbidden(new Error('network down'))).toBe(false)
    expect(isForbidden(undefined)).toBe(false)
    expect(isForbidden('403')).toBe(false)
  })

  it('code 是错误名（字符串），不会和状态码撞上', () => {
    expect(isForbidden(new ApiError(409, 'HTTP 409 for /spaces/7', 'ProjectArchivedError'))).toBe(false)
  })
})

describe('loadFailureReason', () => {
  it('有那句话就给那句话', () => {
    expect(loadFailureReason(new Error('HTTP 403 for /library'))).toBe('HTTP 403 for /library')
  })

  it('空话、没说法的都不给：调用方只显示标题', () => {
    expect(loadFailureReason(new Error('   '))).toBeNull()
    expect(loadFailureReason(null)).toBeNull()
    expect(loadFailureReason({})).toBeNull()
  })
})
