import type { AxiosError, AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios'
import type { ApiEnvelope } from '../cx_types'

import axios from 'axios'

import { t } from '../i18n'
import { apiGenerationHeaders, askToReload, CLIENT_OUTDATED } from '../lib/apiGeneration'
import { refusalText } from '../lib/noticeText'
import { refreshSession } from '../lib/session'
import { isTransportFailure, transportFailureMessage } from '../lib/transportFailure'
import AccountService from '../services/account'

import { ApiError } from './http'

// The pilot keeps axios's upload progress, timeouts and refresh behaviour. The
// fetch transport has different read retries and a 200-only envelope contract;
// consolidating those policies belongs to the reviewed full migration plan.
const baseURL = import.meta.env.VITE_API_BASE_URL
export const axiosApi = axios.create({ baseURL, timeout: 30000 })
export const axiosNewApi = axios.create({
  baseURL: import.meta.env.VITE_NEW_API_BASE_URL || baseURL,
  timeout: 30000,
})

type ErrorEnvelope = ApiEnvelope<unknown> & {
  error?: {
    name?: string
    message?: string
    data?: unknown
    i18n?: { key: string; params?: Record<string, unknown> }
  }
}
type Retriable = InternalAxiosRequestConfig & { retriedAfterRefresh?: true }

for (const instance of [axiosApi, axiosNewApi]) {
  instance.interceptors.request.use((config) => {
    config.headers.set(apiGenerationHeaders())
    if (AccountService.loggedIn && AccountService.accessToken) {
      config.headers.Authorization = `Bearer ${AccountService.accessToken}`
    }
    return config
  })
  instance.interceptors.response.use(
    (response) => {
      const expectsJson = response.config.responseType == null || response.config.responseType === 'json'
      if (expectsJson && response.data !== '' && isTransportFailure(response.data)) {
        throw new ApiError(response.status, transportFailureMessage(response.config.method ?? 'get', response.status))
      }
      return response
    },
    async (error: AxiosError<ErrorEnvelope>) => {
      const config = error.config as Retriable | undefined
      if (error.response?.status === 401 && !error.response.config.url?.startsWith('/users/auth')) {
        if (!config || config.retriedAfterRefresh) throw error
        const outcome = await refreshSession()
        if (outcome.kind === 'ok') {
          config.retriedAfterRefresh = true
          config.headers.Authorization = `Bearer ${outcome.token}`
          return instance.request(config)
        }
        // A refused renewal ends the sign-in once, for every stack, where the
        // account service can hear it: it drops the session and takes the person
        // to a sign-in (`lib/session.ts`). Telling him from here as well — and
        // pushing him to the sign-in page before that has happened — only got
        // this tab bounced back by the sign-in page's own guard.
        throw error
      }
      throw mappedError(error)
    }
  )
}

function mappedError(error: AxiosError<ErrorEnvelope>): Error {
  const response = error.response
  const body = response?.data
  const status = response?.status || 500
  if (response && isTransportFailure(body)) {
    return new ApiError(status, transportFailureMessage(response.config.method ?? 'get', status))
  }
  if (!body) {
    return status === 403
      ? new ApiError(status, t('shell.errors.noPermission'))
      : new Error(error.message || t('shell.errors.network'))
  }
  if (body.error?.name === CLIENT_OUTDATED) askToReload()
  const message = body.error?.name
    ? refusalText(body, body.error.message || body.message)
    : body.message || t(status === 403 ? 'shell.errors.noPermission' : 'shell.errors.server')
  return new ApiError(status, message, body.error?.name, undefined, undefined, body.error?.data)
}

/** JSON endpoints return the existing envelope's payload, including 201 uploads.
 *  No extra success-code check or GET retry is added to the old axios policy. */
export async function axiosRequest<T>(config: AxiosRequestConfig, newer = false): Promise<T> {
  const response = await (newer ? axiosNewApi : axiosApi).request<ApiEnvelope<T>>(config)
  return response.data.data
}

/** Commands without a payload have an explicit success result, not the undefined
 * failure sentinel returned by ErrorHandler.withErrorHandling. Payloads are unchanged. */
export async function axiosMutation(config: AxiosRequestConfig, newer = false): Promise<unknown> {
  const payload = await axiosRequest<unknown>(config, newer)
  return payload == null ? true : payload
}

/** The old helper also displays ServerError (a BusinessError subclass).
 *  Raw network and unrefreshed 401 errors still use the caller's fallback. */
export function axiosErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}
