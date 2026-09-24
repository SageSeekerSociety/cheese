import type { AxiosError, InternalAxiosRequestConfig } from 'axios'
import type { ResponseDataType } from '../../types'

import ApiInstance from '../../api'
import { messageFailed } from '../../utils/showMessage'

import { refreshSession } from '@/lib/session'
import router from '@/router'

// Marks a request already retried after a refresh, so a server that keeps
// answering 401 for some other reason gets one retry, not a loop.
// A string key, not a symbol: axios copies a config's own enumerable keys when
// it re-sends one, and symbols are not among them.
type Retriable = InternalAxiosRequestConfig & { retriedAfterRefresh?: true }

/** A request answered 401: refresh the session and send it once more. */
export default async function refreshToken(error: AxiosError<ResponseDataType>) {
  const config = error.config as Retriable | undefined
  if (!config || config.retriedAfterRefresh) return Promise.reject(error)

  const outcome = await refreshSession()
  if (outcome.kind === 'ok') {
    config.retriedAfterRefresh = true
    config.headers.Authorization = `Bearer ${outcome.token}`
    // config 自带 baseURL（axios 以它覆盖实例默认值），任一实例重发都等价。
    return ApiInstance.request<unknown>(config)
  }
  if (outcome.kind === 'rejected') {
    // The sign-in is over; the account service has already let go of it.
    messageFailed('身份过期，请重新登录')
    router.replace('/account/signin')
  }
  return Promise.reject(error)
}
