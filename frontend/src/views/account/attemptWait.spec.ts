import { afterEach, describe, expect, it } from 'vitest'

import { attemptMessage } from './attemptWait'

import { setLocale } from '@/i18n'
import { BusinessError } from '@/network/types/error'

function refusal(data: Record<string, unknown>, status = 403) {
  return new BusinessError('English text from the server', status, { name: 'ForbiddenError', message: '', data })
}

afterEach(() => setLocale('en'))

describe('the wait a refused attempt states', () => {
  it.each([
    [1, '1 second'],
    [30, '30 seconds'],
    [59, '59 seconds'],
    [59.2, '1 minute'],
    [60, '1 minute'],
    [61, '2 minutes'],
    [300, '5 minutes'],
  ])('%s seconds reads as %s', (seconds, wait) => {
    setLocale('en')
    expect(attemptMessage(refusal({ reason: 'too_many_attempts', retryAfterSeconds: seconds }))).toBe(
      `Too many attempts. Try again in ${wait}.`
    )
  })

  it('is written in the interface language, not the server’s', () => {
    setLocale('zh-CN')
    expect(attemptMessage(refusal({ reason: 'too_many_attempts', retryAfterSeconds: 45 }))).toBe(
      '尝试次数过多，请在 45 秒后重试'
    )
    expect(attemptMessage(refusal({ reason: 'invalid_credentials', retryAfterSeconds: 120 }, 401))).toBe(
      '用户名或密码错误，请在 2 分钟后重试'
    )
    expect(attemptMessage(refusal({ reason: 'invalid_credentials' }, 401))).toBe('用户名或密码错误')
  })

  it('words a mailed code that is wrong, and a new one asked for too soon', () => {
    setLocale('zh-CN')
    expect(attemptMessage(refusal({ reason: 'invalid_email_code' }, 401))).toBe('验证码不正确或已过期')
    expect(attemptMessage(refusal({ reason: 'email_code_too_soon', retryAfterSeconds: 42 }, 400))).toBe(
      '请在 42 秒后重新获取验证码'
    )
    setLocale('en')
    expect(attemptMessage(refusal({ reason: 'email_code_too_soon', retryAfterSeconds: 600 }, 400))).toBe(
      'Request a new code in 10 minutes.'
    )
  })

  it('leaves any other error to the screen', () => {
    expect(attemptMessage(refusal({ reason: 'invalid_code', attemptsRemaining: 3 }, 401))).toBeNull()
    expect(attemptMessage(new BusinessError('Not found', 404))).toBeNull()
    expect(attemptMessage(new Error('network down'))).toBeNull()
  })
})
