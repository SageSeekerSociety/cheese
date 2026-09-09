import type { AxiosResponse } from 'axios'

import { beforeEach, describe, expect, it } from 'vitest'

import responseInterceptor from './responseInterceptor'

import { setLocale } from '@/i18n'
import { ServerError } from '@/network/types/error'

beforeEach(() => setLocale('zh-CN'))

describe('responseInterceptor', () => {
  it('a 200 whose body is a page is not an answer', () => {
    // 强制门户、SPA 自己的兜底页都用 200 答一页 HTML。以前它直接放行，调用方
    // 从字符串上读 `.code` 得到 undefined，在离原因很远的地方才倒下。
    const response = {
      status: 200,
      config: { url: '/tasks', method: 'get' },
      data: '<!DOCTYPE html><html><body>portal</body></html>',
    } as unknown as AxiosResponse

    expect(() => responseInterceptor(response)).toThrowError(
      expect.objectContaining<Partial<ServerError>>({
        name: 'ServerError',
        code: 200,
        message: '服务暂时不可达，请稍后重试（HTTP 200）',
      })
    )
  })

  it('passes the envelope through untouched', () => {
    const response = {
      status: 200,
      config: { url: '/tasks', method: 'get' },
      data: { code: 200, message: 'ok', data: [] },
    } as unknown as AxiosResponse

    expect(responseInterceptor(response)).toBe(response)
  })

  it('passes a blob through: only what was asked for as JSON is judged', () => {
    const response = {
      status: 200,
      config: { url: '/avatars/1', method: 'get', responseType: 'blob' },
      data: new Blob(['png'], { type: 'image/png' }),
    } as unknown as AxiosResponse

    expect(responseInterceptor(response)).toBe(response)
  })

  it('passes a 204 through: nothing is an answer too', () => {
    const response = {
      status: 204,
      config: { url: '/discussions/1', method: 'delete' },
      data: '',
    } as unknown as AxiosResponse

    expect(responseInterceptor(response)).toBe(response)
  })
})
