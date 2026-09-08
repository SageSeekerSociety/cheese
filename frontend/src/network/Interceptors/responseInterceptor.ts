import type { AxiosResponse } from 'axios'

import { ServerError } from '../types/error'

import { isTransportFailure, transportFailureMessage } from '@/lib/transportFailure'

// A 2xx is not yet an answer. A captive portal and the SPA's own fallback both
// say 200 with a page for a body, and `.code` read off that string is
// `undefined` — the caller then fails somewhere far from the cause.
//
// Only a response that was asked for as JSON is judged. The avatar calls ask
// for a `blob` and would fail the object test with a `text` or `arraybuffer`
// answer of their own asking. An empty body is not a page either: the 204s
// (delete a discussion, dismiss a notification) answer with nothing, and that
// is their answer.
export default (response: AxiosResponse) => {
  const { responseType } = response.config
  const expectsJson = responseType == null || responseType === 'json'
  if (expectsJson && response.data !== '' && isTransportFailure(response.data)) {
    throw new ServerError(transportFailureMessage(response.config.method ?? 'get', response.status), response.status)
  }
  return response
}
