import { describe, expect, it } from 'vitest'

import { requestErrorMessage } from './requestErrorMessage'

import { BusinessError, ServerError } from '@/network/types/error'

describe('requestErrorMessage', () => {
  it('shows validation messages returned as BusinessError', () => {
    const error = new BusinessError('邀请码已用完', 422, {
      name: 'UnprocessableEntityError',
      message: '邀请码已用完',
    })

    expect(requestErrorMessage(error, '注册失败')).toBe('邀请码已用完')
  })

  it('continues to show ServerError messages', () => {
    expect(requestErrorMessage(new ServerError('服务暂时不可用', 503), '登录失败')).toBe('服务暂时不可用')
  })

  it('does not expose unexpected error details', () => {
    expect(requestErrorMessage(new Error('socket internals'), '登录失败，请重试')).toBe('登录失败，请重试')
  })
})
