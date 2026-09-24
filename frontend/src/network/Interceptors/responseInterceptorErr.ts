import type { AxiosError } from 'axios'
import type { ResponseDataType } from '../types/index'

import { BusinessError, ServerError } from '../types/error'

import refreshToken from './hooks/refreshToken'

import { isTransportFailure, transportFailureMessage } from '@/lib/transportFailure'

export default (error: AxiosError<ResponseDataType>) => {
  const statusCode = error.response?.status
  const path = error.response?.config.url

  // 处理特殊错误
  if (statusCode === 401) {
    if (path?.startsWith('/users/auth')) {
      throw createError(error)
    }
    // Token 过期，尝试刷新
    return refreshToken(error)
  }

  // The edge answered in place of the app: a page for a body. Reading
  // `.message` off an HTML string is how 「服务器错误」 reached a hackathon room
  // and sent it asking whether the backend was down. A JSON envelope with any
  // status is the app's own answer and keeps its sentence, below.
  if (error.response && isTransportFailure(error.response.data)) {
    throw new ServerError(
      transportFailureMessage(error.response.config.method ?? 'get', error.response.status),
      error.response.status
    )
  }

  if (statusCode === 403) {
    throw createBusinessError(error)
  }

  // 抛出适当类型的错误
  throw createError(error)
}

// 创建业务错误对象
function createBusinessError(error: AxiosError<ResponseDataType>): Error {
  const response = error.response?.data

  // 处理带有详细错误信息的响应
  if (response?.error?.name) {
    return new BusinessError(response.error.message || response.message, 403, response.error)
  }

  // 返回通用业务错误
  // messageFailed(response?.message || '无权限执行此操作')
  return new BusinessError(response?.message || '无权限执行此操作', 403)
}

// 创建一般错误对象
function createError(error: AxiosError<ResponseDataType>): Error {
  const response = error.response?.data
  const statusCode = error.response?.status || 500

  if (!response) {
    return new Error(error.message || '网络请求失败')
  }

  // 处理带有详细错误信息的响应
  if (response.error?.name) {
    return new BusinessError(response.error.message || response.message, statusCode, response.error)
  }

  // 其他服务器错误
  // messageFailed(response.message || '服务器错误')
  return new ServerError(response.message || '服务器错误', statusCode)
}
