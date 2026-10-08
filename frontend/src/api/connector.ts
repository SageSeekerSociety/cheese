import { refusalWords } from '../lib/noticeText'

import { ApiError, authHeaders } from './http'

// The connector lives at the origin root (`/connector/*`), not under `/api`, and its
// responses are plain JSON (no ApiEnvelope). This mirrors `request` but skips the
// `/api` prefix + envelope unwrap. Still sends the Bearer token for owner-gated routes.
export async function connectorRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/connector${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    let name: string | undefined
    try {
      const body = await res.json()
      message = refusalWords(body) || body?.detail || message
      name = body?.error?.name
    } catch {
      // non-JSON error body — keep the status message
    }
    throw new ApiError(res.status, message, name)
  }
  return (await res.json()) as T
}
