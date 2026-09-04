import type { AxiosError } from 'axios'
import type { ResponseDataType } from '@/network/types'

import { describe, expect, it, vi } from 'vitest'

import responseInterceptorErr from './responseInterceptorErr'

import { BusinessError, ServerError } from '@/network/types/error'

vi.mock('./hooks/refreshToken', () => ({ default: vi.fn() }))

describe('responseInterceptorErr', () => {
  it('uses the human-facing business message instead of the error-class prefix', () => {
    const error = {
      response: {
        status: 422,
        config: { url: '/users' },
        data: {
          code: 422,
          message: 'UnprocessableEntityError: Invite code is required',
          error: {
            name: 'UnprocessableEntityError',
            message: 'Invite code is required',
          },
        },
      },
    } as AxiosError<ResponseDataType>

    expect(() => responseInterceptorErr(error)).toThrowError(
      expect.objectContaining<Partial<BusinessError>>({
        message: 'Invite code is required',
        name: 'UnprocessableEntityError',
      })
    )
  })

  it('an HTML page behind a 530 is the edge speaking, not the server: one honest sentence', () => {
    // 黑客松现场：Cloudflare 隧道抖动，用 error 1033 那页 HTML 答 530。以前这里
    // 从字符串上读 `.message`，读不到就说「服务器错误」，房间里的人以为后端挂了。
    const error = {
      response: {
        status: 530,
        config: { url: '/tasks', method: 'get' },
        data: '<!DOCTYPE html><html><body><h1>Argo Tunnel error</h1></body></html>',
      },
    } as unknown as AxiosError<ResponseDataType>

    expect(() => responseInterceptorErr(error)).toThrowError(
      expect.objectContaining<Partial<ServerError>>({
        name: 'ServerError',
        code: 530,
        message: '服务暂时不可达，请稍后重试（HTTP 530）',
      })
    )
  })

  it('a write behind a 502 page says the operation did not land', () => {
    const error = {
      response: {
        status: 502,
        config: { url: '/comments', method: 'post' },
        data: '<html><body>502 Bad Gateway</body></html>',
      },
    } as unknown as AxiosError<ResponseDataType>

    expect(() => responseInterceptorErr(error)).toThrowError(
      expect.objectContaining<Partial<ServerError>>({
        name: 'ServerError',
        code: 502,
        message: '服务暂时不可达，刚才的操作没有送达，请稍后重试（HTTP 502）',
      })
    )
  })

  it("a 5xx envelope is the app's own answer and keeps its sentence", () => {
    // 后端自己的 500 也是信封，里面那句是写给用户的（github_account_link 就这么答）。
    const error = {
      response: {
        status: 500,
        config: { url: '/users/me/github/link', method: 'post' },
        data: { code: 500, message: '暂时无法发起 GitHub 账号连接，请稍后重试', data: null },
      },
    } as unknown as AxiosError<ResponseDataType>

    expect(() => responseInterceptorErr(error)).toThrowError(
      expect.objectContaining<Partial<ServerError>>({
        code: 500,
        message: '暂时无法发起 GitHub 账号连接，请稍后重试',
      })
    )
  })
})
