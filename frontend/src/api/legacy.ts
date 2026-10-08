import type { ApiEnvelope } from '../cx_types'

import { isTransportFailure, readJson, transportFailureMessage } from '../lib/transportFailure'

import { ApiError, authHeaders, refusalError } from './http'

// Mirrors `request`'s envelope unwrap and auth header, minus the GET retry.
//
// It exists because 1.0 was single-prefixed while 2.0 was doubled, and that
// reason is gone: since #370 step 2 `BASE` is `/api` too, so the two differ
// ONLY by that retry. Folding them together is worth doing and is not a
// rename — it decides whether 1.0 calls start being retried, or 2.0 calls stop
// being — so it wants its own change, not a drive-by.
export async function legacyRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  })
  const body = await readJson(res)
  if (isTransportFailure(body)) {
    throw new ApiError(res.status, transportFailureMessage(method, res.status))
  }
  if (!res.ok) {
    throw refusalError(res, body, path)
  }
  const envelope = body as ApiEnvelope<T>
  if (envelope.code !== 200) {
    throw new ApiError(res.status, envelope.message || `API error code ${envelope.code}`)
  }
  return envelope.data
}
