// 连接 GitHub 账号 (#192 user-to-server link, github_account_link.py) — pure
// helpers kept out of the view so they're testable without mounting Vuetify.
import type { OAuthConnectionInfo } from '../cx_types'

export const GITHUB_APP_PROVIDER_ID = 'github_app'

// The list endpoint returns every provider a user has linked; this picks the
// one this section cares about.
export function findGithubAccountConnection(connections: OAuthConnectionInfo[]): OAuthConnectionInfo | null {
  return connections.find((c) => c.providerId === GITHUB_APP_PROVIDER_ID) ?? null
}

// tokenExpires is null whenever the GitHub App wasn't configured to expire
// user tokens — that's "unknown/doesn't expire", never "expired". Only a
// timestamp that has actually passed counts.
export function isGithubAccountTokenExpired(conn: OAuthConnectionInfo, now: Date = new Date()): boolean {
  if (!conn.tokenExpires) return false
  const expires = new Date(conn.tokenExpires).getTime()
  if (Number.isNaN(expires)) return false
  return expires <= now.getTime()
}
