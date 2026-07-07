// Shared transport for the browser-side `/connector/...` calls (chat threads, presence,
// 我的 Agent). These address the backend directly and return raw JSON (no {code,message,data}
// envelope), so they use `fetch` rather than the axios REST instance — which means they also
// bypass the axios refresh-token interceptor.
//
// The access token expires (~15 min) while a chat/workspace tab stays open. Without a refresh
// here, every poll silently 401s and the view freezes until a full page reload. So on a 401 we
// refresh the access token once and retry the request. A single in-flight refresh is shared, so
// concurrent polls (messages + members + presence) don't stampede the refresh endpoint.

import AccountService from '@/services/account'

import { UserApi } from './users'

function authHeaders(json = false): Record<string, string> {
  const h: Record<string, string> = {}
  if (json) h['Content-Type'] = 'application/json'
  const token = AccountService.accessToken
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

let refreshing: Promise<string | null> | null = null
async function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const {
          data: { accessToken, user },
        } = await UserApi.refreshAccessToken()
        if (!accessToken || !user) return null
        await AccountService.login(accessToken, user)
        return accessToken
      } catch {
        return null
      }
    })()
    void refreshing.finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

/** fetch + one transparent retry after refreshing an expired access token. */
export async function authFetch(
  url: string,
  init: RequestInit = {},
  json = false,
): Promise<Response> {
  const r = await fetch(url, { ...init, headers: { ...authHeaders(json), ...init.headers } })
  if (r.status !== 401) return r
  const token = await refreshAccessToken()
  if (!token) return r
  return fetch(url, { ...init, headers: { ...authHeaders(json), ...init.headers } })
}

/** GET a connector endpoint returning raw JSON; throws on non-2xx. */
export async function connectorGet<T>(url: string): Promise<T> {
  const r = await authFetch(url)
  if (!r.ok) throw new Error(`GET ${url} → ${r.status}`)
  return (await r.json()) as T
}

/** Send a connector request with a JSON body; throws with the server message on non-2xx. */
export async function connectorSend<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await authFetch(
    url,
    { method, body: body === undefined ? undefined : JSON.stringify(body) },
    true,
  )
  const data = (await r.json().catch(() => ({}))) as T & { message?: string }
  if (!r.ok) throw new Error(data?.message ?? `${method} ${url} → ${r.status}`)
  return data
}
