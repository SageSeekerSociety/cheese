import { describe, expect, it } from 'vitest'

import { isForbidden, loadFailureReason } from './loadFailure'

import { BusinessError } from '@/network/types/error'

/** `ApiError` 那边的形状：状态码在 `status`，`code` 是给具体错误用的字符串。 */
class FakeApiError extends Error {
  status: number
  code: string
  constructor(status: number, code = '') {
    super(`HTTP ${status}`)
    this.status = status
    this.code = code
  }
}

describe('isForbidden', () => {
  it('认两条来路的 403：ApiError 的 status 和 BusinessError 的 code', () => {
    expect(isForbidden(new FakeApiError(403))).toBe(true)
    expect(isForbidden(new BusinessError('只有管理员能做', 403))).toBe(true)
  })

  it('401 也算没权限：再试一次还是没登录', () => {
    expect(isForbidden(new FakeApiError(401))).toBe(true)
  })

  it('别的失败不算：它们值得再试一次', () => {
    expect(isForbidden(new FakeApiError(500))).toBe(false)
    expect(isForbidden(new Error('network down'))).toBe(false)
    expect(isForbidden(undefined)).toBe(false)
    expect(isForbidden('403')).toBe(false)
  })

  it('ApiError 的 code 是字符串，不会和状态码撞上', () => {
    expect(isForbidden(new FakeApiError(409, 'ProjectArchivedError'))).toBe(false)
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
