import type { AxiosError } from 'axios'
import type { ResponseDataType } from '@/network/types'

import { describe, expect, it, vi } from 'vitest'

import responseInterceptorErr from './responseInterceptorErr'

import { BusinessError } from '@/network/types/error'

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
})
