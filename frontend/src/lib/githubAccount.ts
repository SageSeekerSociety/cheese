// The GitHub App login connection also supplies repository access and PR credit.
import type { OAuthConnectionInfo } from '../cx_types'

import { t } from '@/i18n'

export const GITHUB_APP_PROVIDER_ID = 'github_app'

// The list endpoint returns every provider a user has linked; this picks the
// one this section cares about.
export function findGithubAccountConnection(connections: OAuthConnectionInfo[]): OAuthConnectionInfo | null {
  return connections.find((c) => c.providerId === GITHUB_APP_PROVIDER_ID) ?? null
}

const REPO_INSTALL_REASONS = [
  'invalid_state',
  'missing_installation_id',
  'project_not_found',
  'no_accessible_repos',
  'forge_conflict',
  'github_error',
  'access_denied',
  'upstream_not_accessible',
  'repository_selection_required',
  'repository_write_required',
  'internal_error',
]

function explain(reason: string | undefined): string {
  if (!reason) return t('work.projectSettings.github.unknown')
  if (!REPO_INSTALL_REASONS.includes(reason)) return t('work.projectSettings.github.unknownCode', { code: reason })
  return t(`work.projectSettings.github.repo.${reason}`)
}

// `repository_taken` carries the repo and, when the person connecting may see
// it, the name of the project already holding it (`holder`).
export function explainRepoInstallFailure(
  reason: string | undefined,
  details: { repo?: string; holder?: string } = {}
): string {
  if (reason === 'repository_taken') {
    const reasonText = t('work.projectSettings.github.taken', {
      repo: details.repo ?? t('work.projectSettings.github.takenRepo'),
      where: details.holder
        ? t('work.projectSettings.github.takenHolder', { holder: details.holder })
        : t('work.projectSettings.github.takenHidden'),
      next: details.holder
        ? t('work.projectSettings.github.takenNextHolder')
        : t('work.projectSettings.github.takenNextHidden'),
    })
    return t('work.projectSettings.github.repoFailed', { reason: reasonText })
  }
  return t('work.projectSettings.github.repoFailed', { reason: explain(reason) })
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

/** The viewer's own GitHub account, as a row about it shows it. `unknown`: it
 * could not be read, which is not the same as not linked. */
export type OwnGithub =
  | { state: 'unknown' }
  | { state: 'none' }
  | { state: 'expired'; login: string }
  | { state: 'linked'; login: string }

/** Expired means nothing can renew it: an expired access token with a refresh
 * token behind it is renewed the next time it is used. */
export function ownGithub(conn: OAuthConnectionInfo | null, loaded: boolean, now: Date = new Date()): OwnGithub {
  if (!loaded) return { state: 'unknown' }
  if (!conn) return { state: 'none' }
  const login = conn.login ?? conn.providerUserId
  if (isGithubAccountTokenExpired(conn, now) && !conn.hasRefreshToken) return { state: 'expired', login }
  return { state: 'linked', login }
}
