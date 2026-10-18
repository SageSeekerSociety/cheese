// 关联 GitHub (#192 user-to-server link, `api/routes/users/github_account.py`) —
// pure helpers kept out of the view so they're testable without mounting Vuetify.
import type { OAuthConnectionInfo } from '../cx_types'

import { t } from '@/i18n'

export const GITHUB_APP_PROVIDER_ID = 'github_app'

// The list endpoint returns every provider a user has linked; this picks the
// one this section cares about.
export function findGithubAccountConnection(connections: OAuthConnectionInfo[]): OAuthConnectionInfo | null {
  return connections.find((c) => c.providerId === GITHUB_APP_PROVIDER_ID) ?? null
}

// The callback routes hand their outcome back as `?reason=<code>` — an
// identifier meant for us, not a sentence meant for a user. The page used to
// splice the code straight into the text, so a real person got 「连接账号失败：
// already_linked」 (#222, 2026-08-10 incident). Worse, that code is the ONLY
// feedback anyone gets, and it lands in whichever browser followed the link —
// which may not be the one that started the flow. So each code says what
// happened AND what to do about it; an unknown code still names itself, since
// a code we can't explain is more useful than 「原因未知」.
//
// Sources: `users/github_account.py` (account) and `github_install.py` (repo).
const ACCOUNT_LINK_REASONS = ['already_linked', 'oauth_failed', 'github_unreachable', 'invalid_state']

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

function explain(kind: 'account' | 'repo', known: string[], reason: string | undefined): string {
  if (!reason) return t('work.projectSettings.github.unknown')
  if (!known.includes(reason)) return t('work.projectSettings.github.unknownCode', { code: reason })
  return t(`work.projectSettings.github.${kind}.${reason}`)
}

export function explainAccountLinkFailure(reason: string | undefined): string {
  return t('work.projectSettings.github.accountFailed', { reason: explain('account', ACCOUNT_LINK_REASONS, reason) })
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
  return t('work.projectSettings.github.repoFailed', { reason: explain('repo', REPO_INSTALL_REASONS, reason) })
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

// Whether the linked account has to be reconnected: its access token has
// expired and the backend holds no renewal token it can still use
// (`hasRefreshToken`). With one, the backend renews the access token itself the
// next time it needs it, so an expired access token alone is not worth telling
// anyone about.
export function githubAccountNeedsReconnect(conn: OAuthConnectionInfo, now: Date = new Date()): boolean {
  return isGithubAccountTokenExpired(conn, now) && !conn.hasRefreshToken
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
